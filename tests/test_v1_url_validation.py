"""Table-driven checks for spec §4.1 / security policy §1."""
import pytest

from bot.url_validation import URLValidationError, validate_url


@pytest.mark.parametrize("url,platform,resource", [
    ("https://www.youtube.com/watch?v=AbC_123-dEf", "youtube", "AbC_123-dEf"),
    ("https://youtu.be/AbC_123-dEf", "youtube", "AbC_123-dEf"),
    ("https://instagram.com/reel/AbC_123/", "instagram", "AbC_123"),
    ("https://www.instagram.com/p/AbC_123/", "instagram", "AbC_123"),
    ("https://www.tiktok.com/@example.user/video/123456789012345", "tiktok", "123456789012345"),
])
def test_valid(url, platform, resource):
    result = validate_url(url)
    assert result.url == url
    assert result.platform == platform
    assert result.resource_id == resource


@pytest.mark.parametrize("url", [
    "http://youtube.com/watch?v=AbC_123-dEf",
    "https://youtube.com:443/watch?v=AbC_123-dEf",
    "https://user@youtube.com/watch?v=AbC_123-dEf",
    "https://youtube.com.evil.test/watch?v=AbC_123-dEf",
    "https://youtube.com/watch?v=AbC_123-dEf&list=PL123",
    "https://youtube.com/watch?v=AbC_123-dEf&v=AbC_123-dEf",
    "https://youtube.com/watch?v=AbC_123-dEf&t=1",
    "https://youtube.com/playlist?list=123",
    "https://youtu.be/AbC_123-dEf?si=123",
    "https://vm.tiktok.com/123456",
    "https://instagram.com/reel/AbC_123/?utm_source=a",
    "https://tiktok.com/@example/video/123?foo=bar",
    "https://youtube.com/watch?v=AbC_123-dEf#fragment",
    "https://youtube.com/watch?v=AbC_123-dEf\n",
    "https://127.0.0.1/watch?v=AbC_123-dEf",
    "https://youtu.be/AbC_123-dEf https://youtu.be/AbC_123-dEf",
])
def test_rejected(url):
    with pytest.raises(URLValidationError):
        validate_url(url)
