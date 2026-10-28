"""X.509 certificate decoder (PEM / DER / Base64-DER; bundles supported)."""
import re
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa

from ..registry import Action, Field, Result, Tool, ToolError, register
from .base64_text import decode_bytes

MAX_CERTS = 10


def _load(pem_text: str, upload: bytes | None) -> list[x509.Certificate]:
    if upload:
        data = upload
    elif pem_text.strip():
        data = pem_text.strip().encode("utf-8")
        if b"PRIVATE KEY" in data:
            raise ToolError("This looks like a private key. Never paste private keys: only the public certificate.")
    else:
        raise ToolError("Paste a certificate (PEM) or upload a .pem/.crt/.cer/.der file.")
    try:
        if b"-----BEGIN" in data:
            certs = x509.load_pem_x509_certificates(data)
        else:
            raw = data if upload else decode_bytes(data.decode("ascii", "ignore"))
            certs = [x509.load_der_x509_certificate(raw)]
    except (ValueError, ToolError):
        raise ToolError("Could not parse a certificate. Provide PEM, DER or Base64-encoded DER.")
    if len(certs) > MAX_CERTS:
        raise ToolError(f"Too many certificates in bundle (max {MAX_CERTS}).")
    return certs


def _pubkey(cert) -> str:
    k = cert.public_key()
    if isinstance(k, rsa.RSAPublicKey):
        return f"RSA {k.key_size} bit (e={k.public_numbers().e})"
    if isinstance(k, ec.EllipticCurvePublicKey):
        return f"EC {k.curve.name}"
    if isinstance(k, ed25519.Ed25519PublicKey):
        return "Ed25519"
    if isinstance(k, ed448.Ed448PublicKey):
        return "Ed448"
    return type(k).__name__


def _fp(cert, algo) -> str:
    return ":".join(f"{b:02X}" for b in cert.fingerprint(algo))


def _ext(cert, cls):
    try:
        return cert.extensions.get_extension_for_class(cls).value
    except x509.ExtensionNotFound:
        return None


def describe(cert: x509.Certificate) -> str:
    now = datetime.now(timezone.utc)
    nb, na = cert.not_valid_before_utc, cert.not_valid_after_utc
    status = "NOT YET VALID" if now < nb else "EXPIRED" if now > na else f"valid ({(na - now).days} days left)"
    try:
        sig_hash = cert.signature_hash_algorithm.name
    except Exception:
        sig_hash = "n/a"
    lines = [
        f"Subject        : {cert.subject.rfc4514_string()}",
        f"Issuer         : {cert.issuer.rfc4514_string()}",
        f"Self-signed    : {cert.subject == cert.issuer}",
        f"Serial         : {cert.serial_number:X}",
        f"Version        : {cert.version.name}",
        f"Not before     : {nb:%Y-%m-%d %H:%M:%S} UTC",
        f"Not after      : {na:%Y-%m-%d %H:%M:%S} UTC",
        f"Status         : {status}",
        f"Public key     : {_pubkey(cert)}",
        f"Signature      : {cert.signature_algorithm_oid._name} (hash: {sig_hash})",
        f"SHA-256        : {_fp(cert, hashes.SHA256())}",
        f"SHA-1          : {_fp(cert, hashes.SHA1())}",  # nosec - fingerprint display only
    ]
    san = _ext(cert, x509.SubjectAlternativeName)
    if san:
        names = [f"DNS:{n}" for n in san.get_values_for_type(x509.DNSName)]
        names += [f"IP:{n}" for n in san.get_values_for_type(x509.IPAddress)]
        names += [f"Email:{n}" for n in san.get_values_for_type(x509.RFC822Name)]
        lines.append("SAN            : " + ", ".join(map(str, names)))
    bc = _ext(cert, x509.BasicConstraints)
    if bc:
        lines.append(f"Basic constr.  : CA={bc.ca}, pathlen={bc.path_length}")
    ku = _ext(cert, x509.KeyUsage)
    if ku:
        flags = [n for n in ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment",
                             "key_agreement", "key_cert_sign", "crl_sign") if getattr(ku, n)]
        lines.append("Key usage      : " + ", ".join(flags))
    eku = _ext(cert, x509.ExtendedKeyUsage)
    if eku:
        lines.append("Ext. key usage : " + ", ".join(o._name for o in eku))
    return "\n".join(lines)


def handler(action, form, files):
    certs = _load(form.get("input", ""), files.get("file"))
    parts = [f"── Certificate {i + 1} ──\n{describe(c)}" if len(certs) > 1 else describe(c)
             for i, c in enumerate(certs)]
    return Result("\n\n".join(parts))


register(Tool(
    slug="certificate", title="Certificate Decoder",
    description="Inspect X.509 certificates. Only public certificates — never paste private keys.",
    fields=(
        Field("input", "Certificate (PEM or Base64 DER)", placeholder="-----BEGIN CERTIFICATE-----"),
        Field("file", "…or upload file", "file", accept=".pem,.crt,.cer,.der", upload="cert"),
    ),
    actions=(Action("decode", "Decode", True),),
    handler=handler, group="Encoders / Decoders",
))
