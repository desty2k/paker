/*
 * _memimporter - Import Python extension modules from memory (Windows only)
 *
 * Based on _memimporter from py2exe (MIT License, Thomas Heller)
 * with fixes from rkbennett/memimporter for Python 3.12+ support.
 *
 * Key changes from the original paker version:
 * - Replaced _PyImport_FindExtensionObject with sys.modules dict check (3.11+)
 * - Replaced _Py_PackageContext with PyModuleObject struct hack (3.12+)
 * - Replaced Py_VerboseFlag with _Py_GetConfig()->verbose (3.12+)
 * - Fixed PerformBaseRelocation loop bug (from py2exe upstream)
 */

#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0502
#define NTDDI_VERSION 0x05020000
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <windows.h>

static char module_doc[] =
"Importer which can load extension modules from memory";

#include "MyLoadLibrary.h"
#include "actctx.h"

#ifndef STANDALONE
#include "Python-dynload.h"
#endif

#if (PY_VERSION_HEX >= 0x030C0000)

/* For Python 3.12+, _Py_PackageContext is no longer exported.
   We need direct access to PyModuleObject to fix up module names. */
#ifndef Py_BUILD_CORE
typedef struct {
    PyObject ob_base;
    PyObject *md_dict;
    PyModuleDef *md_def;
    void *md_state;
    PyObject *md_weaklist;
    PyObject *md_name;
} PyModuleObject;
#endif

static PyObject *uid_name;

#endif /* PY_VERSION_HEX >= 0x030C0000 */


#if (PY_VERSION_HEX >= 0x03030000)

int do_import(FARPROC init_func, char *modname, PyObject *spec, PyObject **mod)
{
	int res = -1;
	PyObject* (*p)(void);
	PyObject *m = NULL;
	struct PyModuleDef *def;

#if (PY_VERSION_HEX >= 0x030C0000)
	PyModuleObject *mo;
#elif (PY_VERSION_HEX < 0x03070000)
	char *oldcontext;
#else
	const char *oldcontext;
#endif

	PyObject *name = PyUnicode_FromString(modname);
	if (name == NULL)
		return -1;

	/* Check sys.modules instead of _PyImport_FindExtensionObject
	   (removed in Python 3.11) */
	PyObject *modules = PyImport_GetModuleDict();
	if (PyMapping_HasKeyString(modules, modname)) {
		Py_DECREF(name);
		return 0;
	}

	if (init_func == NULL) {
		PyObject *msg = PyUnicode_FromFormat(
			"dynamic module does not define init function (PyInit_%s)",
			modname);
		if (msg != NULL) {
			PyErr_SetImportError(msg, name, NULL);
			Py_DECREF(msg);
		}
		Py_DECREF(name);
		return -1;
	}

#if (PY_VERSION_HEX < 0x030C0000)
	oldcontext = _Py_PackageContext;
	_Py_PackageContext = modname;
#endif

	p = (PyObject*(*)(void))init_func;
	m = (*p)();

#if (PY_VERSION_HEX < 0x030C0000)
	_Py_PackageContext = oldcontext;
#endif

	if (PyErr_Occurred()) {
		Py_DECREF(name);
		return -1;
	}

	/* Multi-phase initialization (PEP 489) */
	if (PyObject_TypeCheck(m, &PyModuleDef_Type)) {
		Py_DECREF(name);
		*mod = PyModule_FromDefAndSpec((PyModuleDef*)m, spec);
		return 2;
	}

	/* Single-phase initialization fallback */
	def = PyModule_GetDef(m);
	if (def == NULL) {
		PyObject *msg = PyUnicode_FromFormat(
			"initialization of %s did not return an extension module",
			modname);
		if (msg) {
			PyErr_SetObject(PyExc_SystemError, msg);
			Py_DECREF(msg);
		}
		Py_DECREF(name);
		return -1;
	}
	def->m_base.m_init = p;

#if (PY_VERSION_HEX >= 0x030C0000)
	/* Fix up module name since we couldn't use _Py_PackageContext.
	   Directly patch the PyModuleObject struct. */
	mo = (PyModuleObject *)m;
	if (strcmp(PyUnicode_AsUTF8(mo->md_name), modname) != 0) {
		if (PyDict_SetItem(mo->md_dict, uid_name, name) != 0) {
			Py_DECREF(name);
			return -1;
		}
		Py_XDECREF(mo->md_name);
		Py_XSETREF(mo->md_name, Py_NewRef(name));
	}
#endif

#if (PY_VERSION_HEX >= 0x030D0000)
	PyObject *mod_dict = PyModule_GetDict(m);
	if (mod_dict != NULL) {
		PyDict_SetItemString(modules, modname, m);
	}
	res = 0;
#elif (PY_VERSION_HEX >= 0x03070000)
	res = _PyImport_FixupExtensionObject(m, name, name, modules);
#else
	res = _PyImport_FixupExtensionObject(m, name, name);
#endif

	Py_DECREF(name);
	return res;
}

#else
# error "Python < 3.3 is not supported"
#endif

#ifndef STANDALONE
extern wchar_t dirname[];
#endif

static PyObject *
import_module(PyObject *self, PyObject *args)
{
	char *initfuncname;
	char *modname;
	char *pathname;
	HMODULE hmem;
	FARPROC init_func;

	ULONG_PTR cookie = 0;
	PyObject *findproc;
	PyObject *spec;
#ifndef STANDALONE
	BOOL res;
#endif

	int imp_res = -1;
	struct PyModuleDef *def;
	PyObject *state;

	if (!PyArg_ParseTuple(args, "sssOO:import_module",
			      &modname, &pathname,
			      &initfuncname,
			      &findproc,
			      &spec))
		return NULL;

	PyObject *m = PyModule_New(modname);

	cookie = _My_ActivateActCtx();

#ifndef STANDALONE
	res = SetDllDirectoryW(dirname);
#endif

	hmem = MyLoadLibrary(pathname, NULL, 0, findproc);

#ifndef STANDALONE
	if (res)
		SetDllDirectory(NULL);
#endif

	_My_DeactivateActCtx(cookie);

	if (!hmem) {
		char *msg;
		PyObject *error;
		FormatMessageA(
			FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM,
			NULL, GetLastError(),
			MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
			(void *)&msg, 0, NULL);
		msg[strlen(msg)-2] = '\0';
		error = PyUnicode_FromFormat(
			"MemoryLoadLibrary failed loading %s: %s (%d)",
			pathname, msg, GetLastError());
		if (error) {
			PyErr_SetObject(PyExc_ImportError, error);
			Py_DECREF(error);
		} else {
			PyErr_Clear();
			PyErr_SetString(PyExc_ImportError,
				"MemoryLoadLibrary failed (unknown error)");
		}
		LocalFree(msg);
		return NULL;
	}

	init_func = MyGetProcAddress(hmem, initfuncname);
	imp_res = do_import(init_func, modname, spec, &m);

	if (imp_res < 0) {
		MyFreeLibrary(hmem);
		return NULL;
	} else if (imp_res == 2) {
		def = PyModule_GetDef(m);
		state = PyModule_GetState(m);
		if (state == NULL) {
			PyModule_ExecDef(m, def);
		}
		return m;
	}

	Py_DECREF(m);
	return PyImport_ImportModule(modname);
}

static PyObject *
get_verbose_flag(PyObject *self, PyObject *args)
{
	PyObject *flags = PySys_GetObject("flags");
	if (flags != NULL) {
		PyObject *verbose = PyObject_GetAttrString(flags, "verbose");
		if (verbose != NULL)
			return verbose;
		PyErr_Clear();
	}
	return PyLong_FromLong(0);
}

static PyMethodDef methods[] = {
	{ "import_module", import_module, METH_VARARGS,
	  "import_module(modname, pathname, initfuncname, finder, spec) -> module" },
	{ "get_verbose_flag", get_verbose_flag, METH_NOARGS,
	  "Return the Py_Verbose flag" },
	{ NULL, NULL },
};

static struct PyModuleDef moduledef = {
	PyModuleDef_HEAD_INIT,
	"_memimporter",
	module_doc,
	-1,
	methods,
	NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC PyInit__memimporter(void)
{
#if (PY_VERSION_HEX >= 0x030C0000)
	uid_name = PyUnicode_FromString("__name__");
#endif
	return PyModule_Create(&moduledef);
}
