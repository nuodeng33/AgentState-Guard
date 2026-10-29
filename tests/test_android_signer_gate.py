"""Published APKs must have exactly the pinned development signer."""
import pytest

from scripts.verify_android_signer import SignerVerificationError, verify_signer_output

CERT = "a" * 64

def test_exact_single_certificate_is_accepted():
    assert verify_signer_output("Signer #1 certificate SHA-256 digest: " + CERT + "\n", CERT) == CERT

@pytest.mark.parametrize("output", ["", "Signer #1 certificate SHA-256 digest: invalid", "Signer #1 certificate SHA-256 digest: " + "b"*64,
    "Signer #1 certificate SHA-256 digest: " + CERT + "\nSigner #2 certificate SHA-256 digest: " + CERT,
    "Signer #1 certificate SHA-256 digest: " + CERT + "\nSigner #2 certificate SHA-256 digest: invalid"])
def test_absent_wrong_or_multiple_signers_are_rejected(output):
    with pytest.raises(SignerVerificationError):
        verify_signer_output(output, CERT)

@pytest.mark.parametrize("certificate", ["", "not-a-certificate", "a"*65])
def test_bad_expected_identity_never_grants_acceptance(certificate):
    with pytest.raises(SignerVerificationError):
        verify_signer_output("Signer #1 certificate SHA-256 digest: " + CERT, certificate)
