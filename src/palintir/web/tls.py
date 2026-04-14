"""TLS certificate management for the web service.

For a local-network deployment we generate a self-signed cert the first time
the service starts. Browsers will show a warning — that's expected; the
teacher adds an exception once. If you deploy behind a reverse proxy
(nginx, Caddy), leave `tls_cert_file` empty and terminate TLS there instead.
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
from pathlib import Path

import structlog

logger = structlog.get_logger()


def _collect_san_entries() -> tuple[list[str], list[str]]:
    """Gather hostnames and IPs to include in the cert's Subject Alternative Names.

    Returns (dns_names, ip_addresses).
    """
    dns_names = ["localhost", "palintir.local"]
    ips = ["127.0.0.1", "::1"]

    try:
        hostname = socket.gethostname()
        if hostname and hostname not in dns_names:
            dns_names.append(hostname)
    except OSError:
        pass

    # Include the current primary IP so the cert is valid for direct-IP access
    # from other devices on the LAN.
    try:
        # This doesn't actually send packets — it just resolves the route
        # to a public address, revealing the local source IP.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            local_ip = s.getsockname()[0]
            if local_ip and local_ip not in ips:
                ips.append(local_ip)
    except OSError:
        pass

    return dns_names, ips


def generate_self_signed_cert(
    cert_path: Path,
    key_path: Path,
    *,
    common_name: str = "palintir.local",
    validity_days: int = 825,  # Apple's max; Chrome warns if longer
) -> bool:
    """Create a self-signed cert + key pair at the given paths.

    Returns True on success, False if the cryptography library is missing.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        logger.error(
            "tls_cert_generation_failed",
            reason="cryptography package not installed; run `pip install cryptography`",
        )
        return False

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate an RSA-2048 key. EC would be smaller/faster but RSA has wider
    # browser/client support for locally-installed roots.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    dns_names, ips = _collect_san_entries()
    san_list: list[x509.GeneralName] = [x509.DNSName(n) for n in dns_names]
    for ip in ips:
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Palintir"),
        ]
    )

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .sign(key, hashes.SHA256())
    )

    # Write cert + key. Key is 0600 so other users on the system can't read it.
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(key_bytes)
    try:
        key_path.chmod(0o600)
    except OSError:
        pass  # Best-effort on platforms that don't support POSIX perms

    logger.info(
        "tls_self_signed_cert_generated",
        cert=str(cert_path),
        key=str(key_path),
        dns_names=dns_names,
        ips=ips,
        validity_days=validity_days,
    )
    return True


def ensure_tls_materials(cert_file: str, key_file: str) -> tuple[str, str] | None:
    """Return a usable (cert, key) pair, creating a self-signed one if missing.

    Returns None if TLS can't be enabled (e.g. cryptography not installed).
    """
    cert_path = Path(cert_file)
    key_path = Path(key_file)

    if cert_path.is_file() and key_path.is_file():
        return str(cert_path), str(key_path)

    if generate_self_signed_cert(cert_path, key_path):
        return str(cert_path), str(key_path)
    return None
