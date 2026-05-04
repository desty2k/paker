import enum
from dataclasses import dataclass

from paker._loaders._base import NativeLoaderPolicy, _resolve_loader_policy
from paker._loaders._resource_paths import AsFileStrategy, resolve_strategy


class DiskPolicy(str, enum.Enum):
    """Controls whether paker writes to disk at runtime.

    MEMORY — never write to disk. as_file raises on Windows.
    AUTO — allow disk when the platform requires it (Windows as_file).
    DISK — always use the OS loader via temp files.
    """
    MEMORY = "memory"
    AUTO = "auto"
    DISK = "disk"


_POLICY_MAP: dict[DiskPolicy, tuple[AsFileStrategy, NativeLoaderPolicy]] = {
    DiskPolicy.MEMORY: (AsFileStrategy.STRICT, NativeLoaderPolicy.AUTO),
    DiskPolicy.AUTO:   (AsFileStrategy.AUTO, NativeLoaderPolicy.AUTO),
    DiskPolicy.DISK:   (AsFileStrategy.TEMPFILE, NativeLoaderPolicy.DISK),
}


def resolve_disk_policy(
    policy: DiskPolicy | str | None,
) -> DiskPolicy | None:
    if policy is None:
        return None
    if isinstance(policy, DiskPolicy):
        return policy
    try:
        return DiskPolicy(policy.lower())
    except (ValueError, AttributeError):
        raise ValueError(
            f"invalid disk_policy {policy!r}, "
            f"expected {[e.value for e in DiskPolicy]}"
        )


def map_disk_policy(
    policy: DiskPolicy,
) -> tuple[AsFileStrategy, NativeLoaderPolicy]:
    return _POLICY_MAP[policy]


@dataclass(frozen=True)
class RuntimePolicy:
    """Resolved runtime disk behavior for resources and native extensions."""

    as_file_strategy: AsFileStrategy
    native_loader: NativeLoaderPolicy
    explicit_as_file: bool = False
    explicit_native_loader: bool = False

    def differs_from_importer(self, importer) -> bool:
        """Return True when explicit policy inputs require a new importer."""
        if (
            self.explicit_as_file
            and importer._as_file_strategy is not self.as_file_strategy
        ):
            return True
        return (
            self.explicit_native_loader
            and importer._native_loader_policy is not self.native_loader
        )


def resolve_runtime_policy(
    *,
    disk_policy: DiskPolicy | str | None = None,
    as_file_strategy: AsFileStrategy | str | None = None,
    native_loader: NativeLoaderPolicy | str | None = None,
) -> RuntimePolicy:
    """Resolve the public disk-policy knobs into one runtime policy.

    ``disk_policy`` is the simple user-facing knob. The lower-level
    ``as_file_strategy`` and ``native_loader`` parameters override only their
    own subsystem when explicitly provided.
    """
    resolved_policy = resolve_disk_policy(disk_policy)
    explicit_as_file = disk_policy is not None or as_file_strategy is not None
    explicit_native_loader = disk_policy is not None or native_loader is not None

    if resolved_policy is not None:
        default_as_file, default_native = map_disk_policy(resolved_policy)
        if as_file_strategy is None:
            as_file_strategy = default_as_file
        if native_loader is None:
            native_loader = default_native

    return RuntimePolicy(
        as_file_strategy=resolve_strategy(as_file_strategy),
        native_loader=_resolve_loader_policy(native_loader),
        explicit_as_file=explicit_as_file,
        explicit_native_loader=explicit_native_loader,
    )
