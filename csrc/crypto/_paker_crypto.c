/*
 * _paker_crypto — C extension for secure module decryption.
 *
 * Design: "Pass the Sink". Python creates the destination (file
 * descriptor, or requests a code object); C decrypts into a heap buffer,
 * consumes it immediately (write to fd, unmarshal, or compile), zeroes
 * the buffer, and frees. No runtime function returns plaintext as a
 * Python bytes object.
 *
 * Runtime API (exposed to Python):
 *   compile_encrypted(blob, key, filename) → code object    # .py source path
 *   decrypt_and_unmarshal(blob, key) → code object          # .pyc marshal path
 *   decrypt_to_fd(blob, key, fd) → None                     # native loader path
 *   decrypt_into_bytearray(blob, key, dest) → None          # Windows fallback path
 *   encrypt_for_pack(plaintext, key, nonce) → bytes         # packer-only
 *   scrub(bytearray) → None
 *
 * Key handling: the user-supplied key is copied into a stack-adjacent
 * region, mlock'd to keep it off swap, used, then explicit_bzero'd. On
 * macOS the default mlock limit is 64 KB per process — the 32-byte key
 * fits inside the cap; mlock failure (e.g. `ulimit -l 0`) is tolerated
 * and we proceed with the zero at end of scope.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <marshal.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>

#if defined(_WIN32)
  #include <windows.h>
  #include <io.h>
  #define PAKER_WRITE(fd, buf, n) _write((fd), (buf), (unsigned int)(n))
#else
  #include <sys/mman.h>
  #include <unistd.h>
  #define PAKER_WRITE(fd, buf, n) write((fd), (buf), (n))
#endif

#define AES256 1
#define CTR    1
#define CBC    0
#define ECB    0
#include "aes.h"

#define NONCE_SIZE 16
#define KEY_SIZE   32

/* ---------------- memory-safety helpers ---------------- */

static void
paker_bzero(volatile void *ptr, size_t len)
{
#if defined(_WIN32)
    SecureZeroMemory((PVOID)ptr, len);
#elif defined(__APPLE__) || defined(__linux__)
    /*
     * `explicit_bzero` is the idiomatic opt-out-of-optimization zero on
     * Linux glibc >= 2.25 and macOS 13+ (Ventura). For older systems, fall
     * back to a volatile-pointer loop the compiler can't elide.
     */
    #if defined(__GLIBC__) && (__GLIBC__ > 2 || (__GLIBC__ == 2 && __GLIBC_MINOR__ >= 25))
        explicit_bzero((void *)ptr, len);
    #elif defined(__APPLE__) && defined(__clang__)
        /* memset_s is available on macOS but the API is awkward; the
         * volatile loop below is portable and exactly as effective. */
        volatile unsigned char *p = (volatile unsigned char *)ptr;
        while (len--) { *p++ = 0; }
    #else
        volatile unsigned char *p = (volatile unsigned char *)ptr;
        while (len--) { *p++ = 0; }
    #endif
#else
    volatile unsigned char *p = (volatile unsigned char *)ptr;
    while (len--) { *p++ = 0; }
#endif
}

/*
 * Try to lock `addr` into RAM so it doesn't hit swap. Returns 1 on success,
 * 0 on failure (caller should still scrub but without the swap guarantee).
 * On macOS the default `ulimit -l` is 64 KB per process, which comfortably
 * fits our 32-byte key; if a hostile environment has set it to 0, mlock
 * fails with EAGAIN and we proceed without it.
 */
static int
paker_mlock(void *addr, size_t len)
{
#if defined(_WIN32)
    return VirtualLock(addr, len) ? 1 : 0;
#else
    return mlock(addr, len) == 0 ? 1 : 0;
#endif
}

static void
paker_munlock(void *addr, size_t len)
{
#if defined(_WIN32)
    VirtualUnlock(addr, len);
#else
    munlock(addr, len);
#endif
}

/* ---------------- integrity self-check (forward decls) ---------------- */

static int check_integrity(void);

/* ---------------- secure buffer allocation ---------------- */

/*
 * secure_buffer_alloc returns a page-aligned anonymous mapping pinned to
 * RAM and advised against inclusion in core dumps and fork copies. Use for
 * short-lived plaintext so that:
 *   - the OS cannot page it to swap during CPython parsing/compiling,
 *   - a crash dump does not include it,
 *   - an accidental fork does not propagate it to the child.
 *
 * Returns NULL on allocation failure. The returned pointer is not
 * interchangeable with ``malloc``'d memory — free with secure_buffer_free,
 * passing the same ``len`` and the map size (rounded up to page size).
 */
#if !defined(_WIN32)
#include <sys/mman.h>
#if defined(MADV_DONTDUMP) || defined(MADV_WIPEONFORK)
#include <sys/types.h>
#endif
#endif

typedef struct {
    void *ptr;
    size_t map_size;
    int mlocked;
} secure_buffer_t;

static size_t
paker_page_size(void)
{
#if defined(_WIN32)
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return (size_t)si.dwPageSize;
#else
    long ps = sysconf(_SC_PAGESIZE);
    return (size_t)(ps > 0 ? ps : 4096);
#endif
}

static int
secure_buffer_alloc(secure_buffer_t *out, size_t len)
{
    if (len == 0) {
        out->ptr = NULL;
        out->map_size = 0;
        out->mlocked = 0;
        return 0;
    }
    size_t page = paker_page_size();
    size_t map_size = ((len + page - 1) / page) * page;

#if defined(_WIN32)
    /* VirtualAlloc gives a page-aligned commit; VirtualLock pins it in RAM. */
    void *p = VirtualAlloc(NULL, map_size,
                           MEM_COMMIT | MEM_RESERVE,
                           PAGE_READWRITE);
    if (p == NULL) {
        return -1;
    }
    int locked = VirtualLock(p, map_size) ? 1 : 0;
#else
    void *p = mmap(NULL, map_size, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) {
        return -1;
    }
    int locked = (mlock(p, map_size) == 0) ? 1 : 0;
  #ifdef MADV_DONTDUMP
    madvise(p, map_size, MADV_DONTDUMP);
  #endif
  #ifdef MADV_WIPEONFORK
    madvise(p, map_size, MADV_WIPEONFORK);
  #endif
#endif

    out->ptr = p;
    out->map_size = map_size;
    out->mlocked = locked;
    return 0;
}

static void
secure_buffer_free(secure_buffer_t *buf)
{
    if (buf->ptr == NULL || buf->map_size == 0) {
        return;
    }
    paker_bzero(buf->ptr, buf->map_size);
#if defined(_WIN32)
    if (buf->mlocked) {
        VirtualUnlock(buf->ptr, buf->map_size);
    }
    VirtualFree(buf->ptr, 0, MEM_RELEASE);
#else
    if (buf->mlocked) {
        munlock(buf->ptr, buf->map_size);
    }
    munmap(buf->ptr, buf->map_size);
#endif
    buf->ptr = NULL;
    buf->map_size = 0;
    buf->mlocked = 0;
}

/* ---------------- crypto primitive ---------------- */

/*
 * AES-256-CTR: decrypt `ciphertext` into `plaintext` (in-place ok — we
 * copy in and XOR). `key` is expected to have been locked + will be scrubbed
 * by the caller.
 */
static void
aes256_ctr_xcrypt(const uint8_t *key, const uint8_t *nonce,
                  const uint8_t *ciphertext, size_t ct_len,
                  uint8_t *plaintext)
{
    struct AES_ctx ctx;
    memcpy(plaintext, ciphertext, ct_len);
    AES_init_ctx_iv(&ctx, key, nonce);
    AES_CTR_xcrypt_buffer(&ctx, plaintext, (uint32_t)ct_len);
    paker_bzero(&ctx, sizeof(ctx));
}

/*
 * Parse (blob, key) from METH_VARARGS into a locked key copy + split
 * nonce/ciphertext pointers. Returns 0 on success. Caller must call
 * `scrub_key` on success to zero + unlock.
 */
static int
parse_blob_and_key(PyObject *args,
                   const uint8_t **nonce_out,
                   const uint8_t **ciphertext_out,
                   size_t *ct_len_out,
                   uint8_t key_copy_out[KEY_SIZE],
                   int *key_locked_out,
                   const char **filename_out /* nullable */)
{
    const char *blob_data;
    Py_ssize_t blob_len;
    const char *key_data;
    Py_ssize_t key_len;
    const char *filename = NULL;

    if (filename_out != NULL) {
        if (!PyArg_ParseTuple(args, "y#y#s",
                              &blob_data, &blob_len,
                              &key_data, &key_len,
                              &filename)) {
            return -1;
        }
    } else {
        if (!PyArg_ParseTuple(args, "y#y#",
                              &blob_data, &blob_len,
                              &key_data, &key_len)) {
            return -1;
        }
    }

    if (key_len != KEY_SIZE) {
        PyErr_Format(PyExc_ValueError, "key must be %d bytes (got %zd)",
                     KEY_SIZE, key_len);
        return -1;
    }
    if (blob_len < NONCE_SIZE) {
        PyErr_SetString(PyExc_ValueError, "encrypted blob too short");
        return -1;
    }

    memcpy(key_copy_out, key_data, KEY_SIZE);
    *key_locked_out = paker_mlock(key_copy_out, KEY_SIZE);

    *nonce_out = (const uint8_t *)blob_data;
    *ciphertext_out = (const uint8_t *)blob_data + NONCE_SIZE;
    *ct_len_out = (size_t)(blob_len - NONCE_SIZE);
    if (filename_out != NULL) {
        *filename_out = filename;
    }
    return 0;
}

static void
scrub_key(uint8_t key_copy[KEY_SIZE], int locked)
{
    paker_bzero(key_copy, KEY_SIZE);
    if (locked) {
        paker_munlock(key_copy, KEY_SIZE);
    }
}

/* ---------------- runtime-side exported functions ---------------- */

static PyObject *
paker_compile_encrypted(PyObject *self, PyObject *args)
{
    const uint8_t *nonce, *ciphertext;
    size_t ct_len;
    uint8_t key_copy[KEY_SIZE];
    int key_locked;
    const char *filename;
    uint8_t *plaintext = NULL;
    PyObject *code_obj = NULL;

    if (check_integrity() < 0) {
        return NULL;
    }

    if (parse_blob_and_key(args, &nonce, &ciphertext, &ct_len,
                           key_copy, &key_locked, &filename) < 0) {
        return NULL;
    }

    secure_buffer_t buf;
    if (secure_buffer_alloc(&buf, ct_len + 1) < 0) {
        scrub_key(key_copy, key_locked);
        PyErr_NoMemory();
        return NULL;
    }
    plaintext = (uint8_t *)buf.ptr;

    aes256_ctr_xcrypt(key_copy, nonce, ciphertext, ct_len, plaintext);
    plaintext[ct_len] = '\0';

    code_obj = Py_CompileString((const char *)plaintext, filename, Py_file_input);

    secure_buffer_free(&buf);
    scrub_key(key_copy, key_locked);
    return code_obj;
}

static PyObject *
paker_decrypt_and_unmarshal(PyObject *self, PyObject *args)
{
    const uint8_t *nonce, *ciphertext;
    size_t ct_len;
    uint8_t key_copy[KEY_SIZE];
    int key_locked;
    uint8_t *plaintext = NULL;
    PyObject *code_obj = NULL;

    if (check_integrity() < 0) {
        return NULL;
    }

    if (parse_blob_and_key(args, &nonce, &ciphertext, &ct_len,
                           key_copy, &key_locked, NULL) < 0) {
        return NULL;
    }

    secure_buffer_t buf;
    if (secure_buffer_alloc(&buf, ct_len) < 0) {
        scrub_key(key_copy, key_locked);
        PyErr_NoMemory();
        return NULL;
    }
    plaintext = (uint8_t *)buf.ptr;

    aes256_ctr_xcrypt(key_copy, nonce, ciphertext, ct_len, plaintext);

    code_obj = PyMarshal_ReadObjectFromString((const char *)plaintext,
                                              (Py_ssize_t)ct_len);

    secure_buffer_free(&buf);
    scrub_key(key_copy, key_locked);
    return code_obj;
}

static PyObject *
paker_decrypt_to_fd(PyObject *self, PyObject *args)
{
    const char *blob_data;
    Py_ssize_t blob_len;
    const char *key_data;
    Py_ssize_t key_len;
    int fd;
    uint8_t key_copy[KEY_SIZE];
    int key_locked;
    uint8_t *plaintext = NULL;

    if (check_integrity() < 0) {
        return NULL;
    }

    if (!PyArg_ParseTuple(args, "y#y#i",
                          &blob_data, &blob_len,
                          &key_data, &key_len,
                          &fd)) {
        return NULL;
    }
    if (key_len != KEY_SIZE) {
        PyErr_Format(PyExc_ValueError, "key must be %d bytes (got %zd)",
                     KEY_SIZE, key_len);
        return NULL;
    }
    if (blob_len < NONCE_SIZE) {
        PyErr_SetString(PyExc_ValueError, "encrypted blob too short");
        return NULL;
    }

    memcpy(key_copy, key_data, KEY_SIZE);
    key_locked = paker_mlock(key_copy, KEY_SIZE);

    const uint8_t *nonce = (const uint8_t *)blob_data;
    const uint8_t *ciphertext = (const uint8_t *)blob_data + NONCE_SIZE;
    size_t ct_len = (size_t)(blob_len - NONCE_SIZE);

    secure_buffer_t buf;
    if (secure_buffer_alloc(&buf, ct_len) < 0) {
        scrub_key(key_copy, key_locked);
        PyErr_NoMemory();
        return NULL;
    }
    plaintext = (uint8_t *)buf.ptr;

    aes256_ctr_xcrypt(key_copy, nonce, ciphertext, ct_len, plaintext);

    /* Write in a single syscall when possible. Short writes are possible on
     * sockets / pipes but memfd/tempfile are regular files where write(2)
     * with a ct_len < SSIZE_MAX either completes or sets errno. */
    size_t written = 0;
    int errno_saved = 0;
    while (written < ct_len) {
        Py_ssize_t n = PAKER_WRITE(fd, plaintext + written, ct_len - written);
        if (n < 0) {
            errno_saved = errno;
            break;
        }
        written += (size_t)n;
    }

    secure_buffer_free(&buf);
    scrub_key(key_copy, key_locked);

    if (written < ct_len) {
        errno = errno_saved;
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }
    Py_RETURN_NONE;
}

/*
 * decrypt_into_bytearray(blob, key, dest_bytearray) → None
 *
 * Windows-only fallback path: _memimporter.import_module takes plaintext bytes
 * (it is also our C extension, but enhancing it to accept encrypted blobs is
 * a separate workstream). Rather than returning plaintext as a bytes object
 * (where a monkey-patch wrapper silently captures the return value), we
 * require the caller to allocate the destination bytearray so they can
 * deterministically scrub it after the short-lived use.
 *
 * An attacker monkey-patching this function still has to observe bytearray
 * mutation — harder than wrapping and reading a return value.
 */
static PyObject *
paker_decrypt_into_bytearray(PyObject *self, PyObject *args)
{
    const uint8_t *nonce, *ciphertext;
    size_t ct_len;
    uint8_t key_copy[KEY_SIZE];
    int key_locked;
    PyObject *dest;

    const char *blob_data;
    Py_ssize_t blob_len;
    const char *key_data;
    Py_ssize_t key_len;

    if (check_integrity() < 0) {
        return NULL;
    }

    if (!PyArg_ParseTuple(args, "y#y#O!",
                          &blob_data, &blob_len,
                          &key_data, &key_len,
                          &PyByteArray_Type, &dest)) {
        return NULL;
    }
    if (key_len != KEY_SIZE) {
        PyErr_Format(PyExc_ValueError, "key must be %d bytes (got %zd)",
                     KEY_SIZE, key_len);
        return NULL;
    }
    if (blob_len < NONCE_SIZE) {
        PyErr_SetString(PyExc_ValueError, "encrypted blob too short");
        return NULL;
    }

    memcpy(key_copy, key_data, KEY_SIZE);
    key_locked = paker_mlock(key_copy, KEY_SIZE);

    nonce = (const uint8_t *)blob_data;
    ciphertext = (const uint8_t *)blob_data + NONCE_SIZE;
    ct_len = (size_t)(blob_len - NONCE_SIZE);

    if (PyByteArray_Resize(dest, (Py_ssize_t)ct_len) != 0) {
        scrub_key(key_copy, key_locked);
        return NULL;
    }

    uint8_t *out = (uint8_t *)PyByteArray_AS_STRING(dest);
    aes256_ctr_xcrypt(key_copy, nonce, ciphertext, ct_len, out);
    scrub_key(key_copy, key_locked);
    Py_RETURN_NONE;
}

/* ---------------- packer-only primitive ---------------- */

/*
 * encrypt_for_pack is ONLY used at package-time from paker._crypto.encrypt.
 * It IS a decryption primitive (CTR is symmetric) so it must not be part of
 * the runtime attack surface. We keep it in the same extension for code
 * reuse but document it clearly as build-tool-only.
 *
 * A future hardening step: ship two C extensions — _paker_crypto_runtime
 * (no encrypt) and _paker_crypto_pack (encrypt only). For now we treat the
 * name as a signal and rely on paker shipping without the packer at runtime.
 */
static PyObject *
paker_encrypt_for_pack(PyObject *self, PyObject *args)
{
    const char *plain_data;
    Py_ssize_t plain_len;
    const char *key_data;
    Py_ssize_t key_len;
    const char *nonce_data;
    Py_ssize_t nonce_len;
    uint8_t key_copy[KEY_SIZE];
    int key_locked;
    uint8_t *ciphertext = NULL;
    PyObject *result = NULL;

    if (!PyArg_ParseTuple(args, "y#y#y#",
                          &plain_data, &plain_len,
                          &key_data, &key_len,
                          &nonce_data, &nonce_len)) {
        return NULL;
    }
    if (key_len != KEY_SIZE) {
        PyErr_Format(PyExc_ValueError, "key must be %d bytes (got %zd)",
                     KEY_SIZE, key_len);
        return NULL;
    }
    if (nonce_len != NONCE_SIZE) {
        PyErr_Format(PyExc_ValueError, "nonce must be %d bytes (got %zd)",
                     NONCE_SIZE, nonce_len);
        return NULL;
    }

    memcpy(key_copy, key_data, KEY_SIZE);
    key_locked = paker_mlock(key_copy, KEY_SIZE);

    ciphertext = (uint8_t *)malloc((size_t)plain_len);
    if (!ciphertext) {
        scrub_key(key_copy, key_locked);
        PyErr_NoMemory();
        return NULL;
    }

    aes256_ctr_xcrypt(key_copy, (const uint8_t *)nonce_data,
                      (const uint8_t *)plain_data, (size_t)plain_len, ciphertext);

    result = PyBytes_FromStringAndSize((const char *)ciphertext, plain_len);

    /* The buffer we zero here is ciphertext, not plaintext — safe to expose
     * to Python via the return value. We zero our working copy anyway so
     * the process heap doesn't retain a stray copy. */
    paker_bzero(ciphertext, (size_t)plain_len);
    free(ciphertext);
    scrub_key(key_copy, key_locked);

    return result;
}

/* ---------------- scrub ---------------- */

static PyObject *
paker_scrub(PyObject *self, PyObject *arg)
{
    if (PyByteArray_Check(arg)) {
        char *buf = PyByteArray_AS_STRING(arg);
        Py_ssize_t size = PyByteArray_GET_SIZE(arg);
        if (buf && size > 0) {
            paker_bzero(buf, (size_t)size);
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "scrub() requires a bytearray");
        return NULL;
    }
    Py_RETURN_NONE;
}

/* ---------------- integrity self-check ---------------- */

/*
 * Captured at module init: the C function pointers of every runtime entry
 * point. Before any decrypt call, we re-resolve each entry point through
 * the module's own __dict__ and confirm it still points at the same C
 * function. A Python wrapper (e.g. `m.compile_encrypted = lambda ...`)
 * breaks the `PyCFunction_Check` test; a replacement with a different C
 * builtin breaks the pointer equality test.
 */
typedef struct {
    const char *name;
    PyCFunction func;
} integrity_entry_t;

#define INTEGRITY_ENTRY_COUNT 5
static integrity_entry_t integrity_table[INTEGRITY_ENTRY_COUNT];
static PyObject *paker_module_ref = NULL;
static PyObject *paker_tampering_error = NULL;

static int
check_integrity(void)
{
    if (paker_module_ref == NULL || paker_tampering_error == NULL) {
        PyErr_SetString(PyExc_RuntimeError,
                        "paker crypto integrity table not initialised");
        return -1;
    }

    for (int i = 0; i < INTEGRITY_ENTRY_COUNT; i++) {
        PyObject *attr = PyObject_GetAttrString(paker_module_ref,
                                                integrity_table[i].name);
        if (attr == NULL) {
            PyErr_Clear();
            PyErr_Format(paker_tampering_error,
                         "paker runtime tampering: %s missing",
                         integrity_table[i].name);
            return -1;
        }

        int ok = PyCFunction_Check(attr) &&
                 PyCFunction_GET_FUNCTION(attr) == integrity_table[i].func;
        Py_DECREF(attr);

        if (!ok) {
            PyErr_Format(paker_tampering_error,
                         "paker runtime tampering: %s replaced",
                         integrity_table[i].name);
            return -1;
        }
    }
    return 0;
}

/* ---------------- module init ---------------- */

static PyMethodDef methods[] = {
    {"compile_encrypted", paker_compile_encrypted, METH_VARARGS,
     "compile_encrypted(blob, key, filename) -> code\n\n"
     "Decrypt AES-256-CTR encrypted Python source in C and compile to code "
     "object. Plaintext never exists on the Python heap."},
    {"decrypt_and_unmarshal", paker_decrypt_and_unmarshal, METH_VARARGS,
     "decrypt_and_unmarshal(blob, key) -> code\n\n"
     "Decrypt a marshalled .pyc bytecode blob and deserialize it to a code "
     "object in a single C call. Plaintext never reaches Python."},
    {"decrypt_to_fd", paker_decrypt_to_fd, METH_VARARGS,
     "decrypt_to_fd(blob, key, fd) -> None\n\n"
     "Decrypt and write plaintext to the given file descriptor. Caller owns "
     "the fd (typically from memfd_create or mkstemp) and arranges disposal."},
    {"decrypt_into_bytearray", paker_decrypt_into_bytearray, METH_VARARGS,
     "decrypt_into_bytearray(blob, key, dest) -> None\n\n"
     "Write decrypted plaintext into a caller-allocated bytearray. Caller "
     "must scrub the bytearray after use. Used by the Windows in-memory PE "
     "loader path where _memimporter requires plaintext bytes as input."},
    {"encrypt_for_pack", paker_encrypt_for_pack, METH_VARARGS,
     "encrypt_for_pack(plaintext, key, nonce) -> bytes\n\n"
     "PACKER-ONLY. Symmetric CTR encryption used by the build-time bundler. "
     "Do not call this in the runtime loader — exposing it gives attackers a "
     "decryption primitive via the symmetry of CTR."},
    {"scrub", paker_scrub, METH_O,
     "scrub(obj) -> None\n\n"
     "Zero the internal buffer of a bytearray object."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_paker_crypto",
    "Low-level crypto primitives for paker.",
    -1,
    methods,
    NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC
PyInit__paker_crypto(void)
{
    PyObject *module = PyModule_Create(&moduledef);
    if (module == NULL) {
        return NULL;
    }

    paker_tampering_error = PyErr_NewException(
        "_paker_crypto.PakerTamperingError", PyExc_RuntimeError, NULL);
    if (paker_tampering_error == NULL) {
        Py_DECREF(module);
        return NULL;
    }
    Py_INCREF(paker_tampering_error);
    if (PyModule_AddObject(module, "PakerTamperingError", paker_tampering_error) < 0) {
        Py_DECREF(paker_tampering_error);
        Py_DECREF(module);
        return NULL;
    }

    /*
     * Capture the C function pointers from the method table. Subsequent
     * decrypt calls compare live attribute resolutions against these
     * pointers; any mismatch raises PakerTamperingError.
     */
    struct {
        const char *name;
        PyCFunction func;
    } entries[INTEGRITY_ENTRY_COUNT] = {
        {"compile_encrypted",      paker_compile_encrypted},
        {"decrypt_and_unmarshal",  paker_decrypt_and_unmarshal},
        {"decrypt_to_fd",          paker_decrypt_to_fd},
        {"decrypt_into_bytearray", paker_decrypt_into_bytearray},
        {"scrub",                  paker_scrub},
    };
    for (int i = 0; i < INTEGRITY_ENTRY_COUNT; i++) {
        integrity_table[i].name = entries[i].name;
        integrity_table[i].func = entries[i].func;
    }

    Py_INCREF(module);
    paker_module_ref = module;

    return module;
}
