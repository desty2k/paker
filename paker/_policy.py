import enum

from paker._loaders._base import NativeLoaderPolicy
from paker._loaders._resource_paths import AsFileStrategy


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
