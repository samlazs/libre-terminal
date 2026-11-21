"""terminal/redact.py: no key or token in messages or logs (H2)."""

from _common import check, done

from redact import redact, redact_obj

check("abc123def456" not in redact("https://financialmodelingprep.com/stable/x?apikey=abc123def456&limit=5"), "apikey= in a URL")
check(redact("x?api_key=abc123def456").endswith("api_key=REDACTED"), "api_key=")
check("tok999999" not in redact("https://a.b/c?symbol=X&token=tok999999"), "token=")
check("s3cr3tpass" not in redact('{"password": "s3cr3tpass"}'), "JSON password")
check("zzzzyyyyxxxx" not in redact("Authorization: Bearer zzzzyyyyxxxx1234"), "bearer header")
check(redact("monkey=banana&keyboard=1") == "monkey=banana&keyboard=1", "ordinary words untouched")
check(redact("Missing credential 'fmp_api_key'.") == "Missing credential 'fmp_api_key'.", "key names (not values) untouched")
check(redact_obj({"detail": ["u?apikey=abc123def456"]}) == {"detail": ["u?apikey=REDACTED"]}, "nested structures")
check(redact(None) is None and redact("") == "", "empty input")
# audit C8: BLS registrationkey, other glued key names, URL-encoded literal values
check("0123456789abcdef" not in redact("https://api.bls.gov/x?registrationkey=0123456789abcdef&catalog=true"), "BLS registrationkey=")
check("abcd1234efgh" not in redact("u?subscription-key=abcd1234efgh") and "abcd1234efgh" not in redact("u?appkey=abcd1234efgh"), "subscription-key, appkey")
import redact as _r
_r._known_at[0] = 0
_r._known[:] = []
import os as _os
_os.environ["TEST_API_KEY_C8"] = "ab+cd/ef=gh12345"
check("ab%2Bcd%2Fef%3Dgh12345" not in redact("GET /x?k=ab%2Bcd%2Fef%3Dgh12345") and "ab+cd/ef=gh12345" not in redact("v ab+cd/ef=gh12345"), "URL-encoded key value")
del _os.environ["TEST_API_KEY_C8"]
_r._known_at[0] = 0
done("test_redact")
