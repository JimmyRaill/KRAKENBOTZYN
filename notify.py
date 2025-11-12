import os, json, urllib.request


def alert(msg: str):
    url = os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        print("[ALERT]", msg)
        return
    data = json.dumps({"content": msg}).encode("utf-8")
    req = urllib.request.Request(url,
                                 data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("[ALERT_FAIL]", e)
