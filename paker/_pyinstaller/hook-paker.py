from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

hiddenimports = collect_submodules("paker") + [
    "pkgutil",
    "uuid",
    "tempfile",
    "hmac",
    "hashlib",
    "marshal",
    "base64",
    "zoneinfo",
    "decimal",
    "fractions",
    "csv",
    "email",
    "email.utils",
    "http",
    "http.client",
    "http.cookiejar",
    "_paker_crypto",
]

binaries = collect_dynamic_libs("paker")
