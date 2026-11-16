"""Live end-to-end verification for ProjectStream.

Run against a real uvicorn process:

    python scripts/e2e_live.py --base http://127.0.0.1:8904

Exercises every subsystem the way a real user would: profiles with PIN,
library rescan with a real video + SRT file, quote search, seal-attested
playback-search, a two-member watch party over real WebSockets, TV pairing
with a remote controller, watch-minutes telemetry, announcements, and
per-profile scoping. Prints PASS/FAIL per step and exits non-zero on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


async def run(base: str, workdir: Path) -> None:
    ws_base = base.replace("http", "ws", 1)
    async with httpx.AsyncClient(base_url=base, timeout=20) as c:
        # --- basics ------------------------------------------------------------
        r = await c.get("/healthz")
        check("healthz ok", r.status_code == 200 and r.json()["ok"] is True)

        r = await c.get("/api/status")
        check("status ok", r.status_code == 200 and r.json()["ok"] is True)

        # --- profiles + PIN ----------------------------------------------------
        r = await c.post("/api/profiles", json={"name": "Papa", "avatar": "🦁"})
        p1 = r.json()["profile"]["id"]
        check("profile create (no pin)", r.status_code == 200 and p1 > 0)

        r = await c.post("/api/profiles", json={"name": "Anak", "avatar": "🐸", "pin": "4321"})
        p2 = r.json()["profile"]["id"]
        check("profile create (pin)", r.status_code == 200)

        r = await c.post("/api/profiles/select", json={"id": p2, "pin": "0000"})
        check("profile select wrong pin rejected", r.status_code == 403)

        r = await c.post("/api/profiles/select", json={"id": p2, "pin": "4321"})
        check("profile select ok", r.status_code == 200)

        # --- library rescan with real video + srt ------------------------------
        # The server's PM_LIBRARY_DIRS must include this fixed path; the script
        # writes fixtures there and asks the server to scan.
        media = Path("/tmp/ps-e2e-media")
        media.mkdir(exist_ok=True)
        (media / "Demo Movie 2024.mp4").write_bytes(b"\x00" * 1024)
        (media / "Demo Movie 2024.srt").write_text(
            "1\n00:00:10,000 --> 00:00:13,000\nA quote for testing purposes\n\n"
            "2\n00:00:20,000 --> 00:00:23,000\nSecond line here\n",
            encoding="utf-8",
        )

        r = await c.post("/api/library/rescan")
        data = r.json()
        check("rescan scanned 1 file", data.get("scanned") == 1, str(data))
        check("rescan indexed subtitle lines", (data.get("subtitle_lines") or 0) >= 2, str(data))

        r = await c.get("/api/quote-search", params={"q": "quote for testing"})
        hits = r.json()["results"]
        check("quote search finds the line", len(hits) == 1 and hits[0]["start"] == 10.0, str(r.json()))
        if hits:
            check(
                "quote search deep link",
                "/watch/local/" in hits[0]["watch_url"] and "t=10" in hits[0]["watch_url"],
            )

        # --- seal + playback-search ----------------------------------------------
        seal = (await c.post("/api/guard-attest", json={"client_id": "e2e-client"})).json()["seal"]
        check("guard-attest issues seal", bool(seal) and seal.count("|") == 3)

        r = await c.post("/api/playback-search", json={"tmdb_id": 1, "media_type": "movie"})
        check("playback-search without seal -> SESSION_REBIND_REQUIRED", r.json()["status"] == "SESSION_REBIND_REQUIRED")

        r = await c.post(
            "/api/playback-search",
            json={},
            headers={"X-FS-Seal": seal},
        )
        check("playback-search missing id -> NO_STREAM_LOOKUP_ID", r.json()["status"] == "NO_STREAM_LOOKUP_ID")

        r = await c.post(
            "/api/playback-search",
            json={"tmdb_id": 99999, "media_type": "movie"},
            headers={"X-FS-Seal": seal},
        )
        status = r.json()["status"]
        check("playback-search unknown -> STREAM_UNAVAILABLE", status == "STREAM_UNAVAILABLE")

        # bind the library row to a tmdb id so resolution can find it
        import sqlite3

        from app.config import get_settings  # noqa: E402  (import path of the installed app)

        db = get_settings().db_path
        conn = sqlite3.connect(db)
        conn.execute("UPDATE library SET tmdb_id = 555 WHERE title = 'Demo Movie'")
        conn.commit()
        conn.close()

        r = await c.post(
            "/api/playback-search",
            json={"tmdb_id": 555, "media_type": "movie"},
            headers={"X-FS-Seal": seal},
        )
        body = r.json()
        check("playback-search known -> OK with local source", body["status"] == "OK" and body["sources"], str(body))

        # --- watchlist scoping per profile ---------------------------------------
        await c.post("/api/watchlist", json={"tmdb_id": 100, "media_type": "movie"}, cookies={"ps_profile": str(p1)})
        r = await c.get("/api/watchlist", cookies={"ps_profile": str(p1)})
        check("watchlist scoped (papa sees his)", len(r.json()) == 1)
        r = await c.get("/api/watchlist", cookies={"ps_profile": str(p2)})
        check("watchlist scoped (anak sees none)", len(r.json()) == 0)

        # --- watch party over real WebSockets --------------------------------------
        import websockets

        r = await c.post("/api/party/create", json={"name": "Host"})
        code = r.json()["code"]
        check("party created", bool(code) and len(code) == 6)

        async def party_flow() -> None:
            uri = f"{ws_base}/api/party/ws?code={code}"
            async with websockets.connect(uri) as host_ws, websockets.connect(uri) as guest_ws:
                await host_ws.send(json.dumps({"type": "hello", "name": "Host"}))
                joined = json.loads(await host_ws.recv())
                check("party host joined as host", joined["role"] == "host")
                await host_ws.recv()  # host promotion event

                await guest_ws.send(json.dumps({"type": "hello", "name": "Tamu"}))
                gj = json.loads(await guest_ws.recv())
                check("party guest joined as guest", gj["role"] == "guest")
                await host_ws.recv()  # members broadcast

                await host_ws.send(
                    json.dumps(
                        {
                            "type": "state",
                            "tmdb_id": 555,
                            "library_id": 1,
                            "media_type": "movie",
                            "playing": True,
                            "position": 3.0,
                        }
                    )
                )
                relayed = json.loads(await guest_ws.recv())
                check("party state relayed to guest", relayed["type"] == "state" and relayed["library_id"] == 1)

                await guest_ws.send(json.dumps({"type": "chat", "text": "halo dari guest"}))
                chat = json.loads(await host_ws.recv())
                check("party chat relayed to host", chat["type"] == "chat" and chat["text"].startswith("halo"))

                await guest_ws.send(json.dumps({"type": "play", "position": 5}))
                err = json.loads(await guest_ws.recv())
                check("party guest cannot control", err["type"] == "error")

                await host_ws.send(json.dumps({"type": "pause", "position": 9}))
                relayed = json.loads(await guest_ws.recv())
                check("party pause relayed", relayed["type"] == "pause" and relayed["position"] == 9)

        try:
            await asyncio.wait_for(party_flow(), timeout=15)
        except TimeoutError:
            check("party flow completed", False, "timeout")
        except Exception as exc:  # noqa: BLE001
            check("party flow completed", False, repr(exc))

        # --- TV pairing + remote over real WebSockets --------------------------------
        r = await c.post("/api/tv-pair", json={"device_name": "TV Keluarga"})
        tv_code = r.json()["code"]
        check("tv pairing code issued", bool(tv_code) and len(tv_code) == 4)

        r = await c.post("/api/tv/claim", json={"code": tv_code})
        check("tv claim ok", r.status_code == 200)

        async def tv_flow() -> None:
            screen_uri = f"{ws_base}/api/tv/ws?code={tv_code}&role=screen"
            controller_uri = f"{ws_base}/api/tv/ws?code={tv_code}&role=controller"
            async with websockets.connect(screen_uri) as screen, websockets.connect(controller_uri) as controller:
                s_hello = json.loads(await screen.recv())
                c_hello = json.loads(await controller.recv())
                check("tv both roles paired", s_hello["type"] == "paired" and c_hello["type"] == "paired")

                s_join = json.loads(await screen.recv())
                check("tv screen sees controller", s_join["type"] == "controller-joined")

                await controller.send(json.dumps({"type": "load", "library_id": 1, "title": "Demo"}))
                load = json.loads(await screen.recv())
                check("tv load command relayed", load["type"] == "load" and load["library_id"] == 1)

                await controller.send(json.dumps({"type": "pause"}))
                check("tv pause relayed", json.loads(await screen.recv())["type"] == "pause")

                await screen.send(json.dumps({"type": "state", "playing": False, "position": 7}))
                st = json.loads(await controller.recv())
                check("tv state back to remote", st["type"] == "state" and st["position"] == 7)

        try:
            await asyncio.wait_for(tv_flow(), timeout=15)
        except TimeoutError:
            check("tv flow completed", False, "timeout")
        except Exception as exc:  # noqa: BLE001
            check("tv flow completed", False, repr(exc))

        # --- watch minutes + wrapped --------------------------------------------------
        await c.post(
            "/api/watch-minutes", json={"tmdb_id": 555, "media_type": "movie", "seconds": 1500}, cookies={"ps_profile": str(p1)}
        )
        r = await c.get("/wrapped", cookies={"ps_profile": str(p1)})
        check("wrapped page renders", r.status_code == 200 and "Wrapped" in r.text)

        # --- intro markers --------------------------------------------------------------
        await c.post("/api/intro", json={"tmdb_id": 555, "media_type": "movie", "start": 10, "end": 75})
        r = await c.get("/api/intro", params={"tmdb": 555, "type": "movie"})
        check("intro marker roundtrip", r.json() == {"has_intro": True, "start": 10, "end": 75})

        # --- announcement -----------------------------------------------------------------
        (db.parent / "announcement.json").write_text(
            json.dumps({"text": "Selamat datang di ProjectStream!", "until": "2099-01-01"})
        )
        r = await c.get("/api/announcement")
        check("announcement active", r.json()["active"] is True and "Selamat datang" in r.json()["text"])

        r = await c.get("/")
        check("home shows announcement banner", "Selamat datang" in r.text)

        # --- pages ------------------------------------------------------------------------
        for path in ("/profiles", "/party", "/tv", "/remote", "/wrapped", "/calendar", "/library", "/watchlist"):
            r = await c.get(path)
            check(f"page {path}", r.status_code == 200)

        # --- image proxy rejects foreign urls -----------------------------------------------
        r = await c.get("/api/tmdb-image", params={"url": "https://evil.example.com/x.jpg"})
        check("image proxy rejects foreign url", r.status_code == 400)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8904")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="ps-e2e-") as tmp:
        workdir = Path(tmp)
        asyncio.run(run(args.base, workdir))

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
