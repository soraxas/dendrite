"""Real-time headless Chrome screenshot over CDP (macOS Chrome path): screenshot.py OUT.png URL [wait_s] [w h] [click_selector] [hover_selector]"""

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


async def main(out, url, wait=3.0, w=1500, h=900, click=None, hover=None):
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--remote-debugging-port=9333",
            f"--user-data-dir={prof}",
            f"--window-size={w},{h}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json"))
                break
            except Exception:
                time.sleep(0.2)
        ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            n = 0
            logs = []

            async def call(method, **params):
                nonlocal n
                n += 1
                mid = n
                await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("method") in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown", "Log.entryAdded"):
                        logs.append(m)
                    if m.get("id") == mid:
                        return m.get("result", {})

            await call("Runtime.enable")
            await call("Log.enable")
            await call("Page.enable")
            await call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
            await call("Page.navigate", url=url)
            end = time.time() + wait
            while time.time() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.2))
                    if m.get("method") in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown", "Log.entryAdded"):
                        logs.append(m)
                except TimeoutError:
                    pass
            if hover:
                r = await call(
                    "Runtime.evaluate",
                    returnByValue=True,
                    expression=(
                        f"(() => {{ const r = document.querySelector({json.dumps(hover)})?.getBoundingClientRect();"
                        " return r ? [r.x + r.width / 2, r.y + r.height / 2] : null; })()"
                    ),
                )
                pt = r.get("result", {}).get("value")
                if not pt:
                    print("hover target not found:", hover, r)
                if pt:
                    for dx in (-30, 0):  # move onto the element so mouseenter fires
                        await call("Input.dispatchMouseEvent", type="mouseMoved", x=pt[0] + dx, y=pt[1])
                        await asyncio.sleep(0.15)
                    await asyncio.sleep(0.6)
                    found = await call(
                        "Runtime.evaluate", returnByValue=True, expression="!!document.querySelector('.hovercard')"
                    )
                    print("hover target", pt, "hovercard shown:", found.get("result", {}).get("value"))
            if click:
                await call("Runtime.evaluate", expression=f"document.querySelector({json.dumps(click)})?.click()")
                await asyncio.sleep(1.5)
            shot = await call("Page.captureScreenshot", format="png")
            open(out, "wb").write(base64.b64decode(shot["data"]))
            for m in logs:
                p = m["params"]
                if m["method"] == "Runtime.exceptionThrown":
                    print(
                        "EXCEPTION",
                        p["exceptionDetails"]
                        .get("exception", {})
                        .get("description", p["exceptionDetails"]["text"])[:500],
                    )
                elif m["method"] == "Runtime.consoleAPICalled":
                    print(
                        "CONSOLE",
                        p["type"],
                        " ".join(str(a.get("value", a.get("description", ""))) for a in p["args"])[:500],
                    )
                else:
                    print("LOG", p["entry"]["level"], p["entry"]["text"][:300])
    finally:
        proc.terminate()


a = sys.argv
asyncio.run(
    main(
        a[1],
        a[2],
        float(a[3]) if len(a) > 3 else 3.0,
        *(int(x) for x in a[4:6]) if len(a) > 5 else (1500, 900),
        a[6] if len(a) > 6 and a[6] else None,
        a[7] if len(a) > 7 else None,
    )
)
