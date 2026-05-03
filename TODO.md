# TODO

## Debug mode for tracebacks

`strip_metadata=False` gives `paker://` paths in tracebacks but source
lines are still missing. Add `debug=True` on `dumps()` / `loads()` to
inject source lines into `linecache`.

## Windows `_memimporter` modernization

Shipped: ARM64 support, Static TLS detection + tempfile fallback,
`native_loader="disk"` switch for EDR-sensitive environments.

Remaining: delay-loaded imports, CFG/XFG.

## Pack-time Authenticode signing

Allow vendors to sign .pyd files with their Authenticode certificate
during `paker.dumps()`. Signed bytes get encrypted into the bundle.
At load time, `native_loader="disk"` writes an already-signed .pyd.

## Known limitations

- Packages > 100MB hit JSON serialization memory limits.
- Windows Static TLS extensions (Rust, some C++) require tempfile +
  LoadLibrary — in-memory loading is not achievable from user mode.
- In-memory PE loading (`native_loader="auto"` on Windows) may trigger
  EDR behavioral detections. Use `native_loader="disk"` + allowlisting
  for enterprise environments.
