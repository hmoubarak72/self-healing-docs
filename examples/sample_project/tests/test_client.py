from taskq.client import TaskClient


def test_base_url_strip():
    assert TaskClient("http://x/").base_url == "http://x"
