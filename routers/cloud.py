# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Cloud HTTP routes for the full AutoYou server."""

from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    @admin_app.get("/api/cloud/status")
    async def cloud_status(request: Request):
      """Return current AutoYou Cloud registration and connection status."""
      redir = server._require_login(request)
      if redir:
        return redir
      return JSONResponse(await server._build_cloud_status_snapshot())

    @admin_app.get("/api/cloud/ice-preview")
    async def cloud_ice_preview(request: Request):
      """Diagnostic: show the iceServers this server would advertise on /auth.

      Merges admin-configured iceServers with cloud connection helpers (incl. the user's
      self-pushed user_ice_override). Lets an operator confirm a dashboard
      ICE-override change has propagated without doing a full pairing.
      """
      redir = server._require_login(request)
      if redir:
        return redir
      base = server._get_pairing_ice_servers()
      try:
        merged = await server._get_pairing_ice_servers_async()
      except Exception as exc:  # never 500 a diagnostic
        return JSONResponse({"error": str(exc), "base": base})
      return JSONResponse({
        "base_admin_iceservers": base,
        "advertised_iceservers": merged,
        "cloud_contributed": max(0, len(merged) - len(base)),
      })

    @admin_app.get("/api/cloud/link-start")
    async def cloud_link_start(request: Request):
        """Redirect browser to AutoYou Cloud sign-in / link page."""
        redir = server._require_login(request)
        if redir:
            return redir
        import secrets as _secrets
        state = _secrets.token_urlsafe(16)
        callback_cookie = _secrets.token_urlsafe(32)
        if not hasattr(server.STATE, '_pending_cloud_states'):
            server.STATE._pending_cloud_states = {}
        server.STATE._pending_cloud_states[state] = {
            "created_at": server.time.time(),
            "cookie_hash": server._cloud_link_cookie_hash(callback_cookie),
        }
        callback_url = f"{server._cloud_callback_origin_for_request(request)}/api/cloud/callback"
        redirect_url = f"{server.AUTOYOU_CLOUD_BASE}/v1/server/link?{server.urlencode({'state': state, 'callback': callback_url})}"
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.set_cookie(
            server._CLOUD_LINK_CALLBACK_COOKIE,
            callback_cookie,
            max_age=server._CLOUD_LINK_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            path="/api/cloud/callback",
        )
        return response

    @admin_app.get("/api/cloud/callback")
    async def cloud_callback(request: Request, link_token: str = "", state: str = ""):
        """Receives the redirect back from AutoYou Cloud after sign-in.

        Note: _require_login is intentionally NOT used here.  The callback URL is
        reached via a cross-site redirect from app.autoyou.me, so the browser does
        not include SameSite=Strict cookies.  Instead we verify the ``state``
        parameter plus a short-lived SameSite=Lax flow cookie. The state proves the
        flow was initiated by an authenticated admin session, and the cookie binds
        the callback to the same local browser profile that started the flow.
        """
        # Validate the CSRF / flow-integrity state token
        _pending = getattr(server.STATE, '_pending_cloud_states', {})
        if not state or state not in _pending:
            return server._set_no_store_headers(HTMLResponse(
                "<h2>Invalid or expired link session.</h2>"
                "<p>Please close this tab and start the link flow again from the admin dashboard.</p>",
                status_code=400,
            ))
        pending_state = _pending[state]
        if isinstance(pending_state, dict):
            created_at = float(pending_state.get("created_at", 0.0) or 0.0)
            expected_cookie_hash = str(pending_state.get("cookie_hash") or "")
        else:
            created_at = float(pending_state or 0.0)
            expected_cookie_hash = ""
        if server.time.time() - created_at > server._CLOUD_LINK_MAX_AGE_SECONDS:
            _pending.pop(state, None)
            return server._clear_cloud_link_cookie(server._set_no_store_headers(HTMLResponse(
                "<h2>Link session expired.</h2>"
                "<p>Please start the link flow again from the admin dashboard.</p>",
                status_code=400,
            )))
        callback_cookie = str(request.cookies.get(server._CLOUD_LINK_CALLBACK_COOKIE) or "")
        if not expected_cookie_hash or not callback_cookie or not server.secrets.compare_digest(
            server._cloud_link_cookie_hash(callback_cookie),
            expected_cookie_hash,
        ):
            return server._set_no_store_headers(HTMLResponse(
                "<h2>Invalid link browser session.</h2>"
                "<p>Please close this tab and start the link flow again from the same admin browser profile.</p>",
                status_code=400,
            ))
        _pending.pop(state, None)
        if not link_token:
            return server._clear_cloud_link_cookie(server._set_no_store_headers(HTMLResponse("<h2>Missing link token. Please try again.</h2>", status_code=400)))

        import httpx as _httpx
        try:
            shared_key = server._shared_device_server_key_material(create=True)
            async with _httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{server.AUTOYOU_CLOUD_BASE}/v1/server/register",
                    json={
                        "link_token": link_token,
                        "server_name": (server.STATE.config or {}).get("server", {}).get("name", "AutoYou Server"),
                        **({"publicKey": shared_key[1]} if shared_key else {}),
                    },
                )
            if resp.status_code != 200:
                if resp.status_code == 402:
                    try:
                        detail = resp.json().get("detail") or resp.text
                    except Exception:
                        detail = resp.text
                    return server._clear_cloud_link_cookie(server._set_no_store_headers(HTMLResponse(
                        f"<h2>Subscription required</h2><p>{server.html.escape(str(detail))}</p><p>Activate the same account on AutoYou Cloud, then restart the link flow from this server.</p>",
                        status_code=402,
                    )))
                return server._clear_cloud_link_cookie(server._set_no_store_headers(HTMLResponse(f"<h2>Registration failed: {server.html.escape(resp.text)}</h2>", status_code=502)))
            data = resp.json()
        except Exception as e:
            return server._clear_cloud_link_cookie(server._set_no_store_headers(HTMLResponse(f"<h2>Could not reach AutoYou Cloud: {server.html.escape(str(e))}</h2>", status_code=502)))

        # Save registration data to the already-unlocked config. Never synthesize a
        # default config here, because that would disconnect existing local settings.
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._clear_cloud_link_cookie(server._set_no_store_headers(HTMLResponse(f"<h2>{server.html.escape(block_reason)}</h2>", status_code=409)))
        cfg = server._loaded_config_for_update()
        cfg.setdefault("cloud", {})
        cfg["cloud"]["server_token"] = data.get("server_token", "")
        cfg["cloud"]["server_id"] = data.get("server_id", "")
        cfg["cloud"]["user_id"] = data.get("user_id", "")
        cfg["cloud"]["email"] = data.get("email", "")
        pair_entitlement = data.get("cloud_pair_enabled")
        cfg["cloud"]["pair_enabled"] = bool(pair_entitlement) if isinstance(pair_entitlement, bool) else False
        cfg["cloud"]["pair_entitlement_verified"] = isinstance(pair_entitlement, bool)
        import datetime as _dt
        now_iso = _dt.datetime.utcnow().isoformat() + "Z"
        cfg["cloud"]["registered_at"] = now_iso
        cfg["cloud"]["token_issued_at"] = data.get("token_issued_at", "") or now_iso
        server.STATE.config = server._save_and_reload_state_config(cfg)

        if cfg["cloud"]["pair_enabled"]:
            server.asyncio.create_task(server._start_cloud_sse_listener())

        email = server.html.escape(data.get("email", "your account"))
        if not cfg["cloud"]["pair_entitlement_verified"]:
            linked_title = "Cloud account linked"
            linked_copy = "Cloud Pair access could not be verified. Re-link after the cloud service is updated."
        elif cfg["cloud"]["pair_enabled"]:
            linked_title = "Cloud Pair linked"
            linked_copy = "Paid Cloud Pair and software updates are active."
        else:
            linked_title = "Free account connected"
            linked_copy = "Software updates are active. Paid Cloud Pair remains optional and separate."
        return server._clear_cloud_link_cookie(server._set_no_store_headers(HTMLResponse(f"""<!DOCTYPE html>
    <html><head><meta charset="UTF-8"><title>AutoYou Cloud - Linked</title>
    <style>body{{font-family:-apple-system,sans-serif;background:#0f0f0f;color:#f0f0f0;
    display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
    .card{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:16px;padding:40px;
    max-width:420px;text-align:center}}.icon{{font-size:48px;margin-bottom:16px}}
    h1{{font-size:22px;margin-bottom:8px}}p{{color:#888;margin-bottom:24px}}
    .btn{{display:inline-block;background:#007aff;color:#fff;padding:12px 24px;
    border-radius:8px;text-decoration:none;font-weight:600}}</style></head>
    <body><div class="card"><div class="icon">✅</div>
    <h1>{linked_title}</h1>
    <p>This server is registered to <strong>{email}</strong>. {linked_copy}</p>
    <a href="/" class="btn">Back to Dashboard</a></div></body></html>""")))

    @admin_app.post("/api/cloud/push-client")
    async def cloud_push_client(request: Request):
        """Ask paired clients to reconnect through AutoYou Cloud."""
        redir = server._require_login(request)
        if redir:
            return redir
        result = await server._push_to_client("/request_autopair")
        if not result.get("error"):
            result = {**result, "message": "Client connection request sent."}
        return JSONResponse(result)

    @admin_app.post("/api/cloud/notify-client")
    async def cloud_notify_client(request: Request):
        """Send a notification to the owner's opted-in client devices via AutoYou Cloud."""
        redir = server._require_login(request)
        if redir:
            return redir
        try:
            body = await request.json()
        except Exception:
            body = dict(await request.form())

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            data = {}
        data.setdefault("source", "admin")
        data.setdefault("delivery", "offline_notification")

        result = await server._notify_cloud_client(
            title=str(body.get("title") or "AutoYou").strip(),
            body=str(body.get("body") or "Notification from your AutoYou server.").strip(),
            category=str(body.get("category") or "admin").strip(),
            data=data,
        )
        status_code = int(result.get("status_code") or (200 if result.get("success") else 502))
        if status_code < 400 and result.get("success") is False:
            status_code = 502
        return JSONResponse(result, status_code=status_code)

    @admin_app.post("/api/cloud/unregister")
    async def cloud_unregister(request: Request):
        """Remove cloud registration and stop the SSE listener."""
        redir = server._require_login(request)
        if redir:
            return redir
        block_reason = server._config_write_block_reason()
        if block_reason:
            return PlainTextResponse(block_reason, status_code=409)
        cfg = server._loaded_config_for_update()
        cfg["cloud"] = {
            "server_token": "",
            "server_id": "",
            "user_id": "",
            "email": "",
            "registered_at": "",
            "token_issued_at": "",
        }
        server.STATE.config = server._save_and_reload_state_config(cfg)
        if server.STATE.cloud_sse_task and not server.STATE.cloud_sse_task.done():
            server.STATE.cloud_sse_task.cancel()
            server.STATE.cloud_sse_task = None
        server.STATE.cloud_connected = False
        server.STATE.cloud_token_rejected = False
        return RedirectResponse(url="/?cloud_unregistered=1", status_code=302)

    @admin_app.post("/api/cloud/activate")
    async def cloud_activate(request: Request):
        """Promote this server to the active AutoYou Cloud server for the account."""
        redir = server._require_login(request)
        if redir:
            return redir
        return JSONResponse(await server._activate_current_cloud_server_registration())

    @admin_app.post("/api/cloud/reregister")
    async def cloud_reregister(request: Request):
        """Re-register this server with AutoYou Cloud after token rejection.

        Clears the rejected-token state and restarts the SSE listener if a token
        still exists in config.  If the token has been revoked, the listener will
        stop again and the user should run the full link-start flow from the admin
        dashboard.

        This is the programmatic equivalent of clicking the Cloud Pair button a
        second time from a device that was previously replaced by another instance.
        """
        redir = server._require_login(request)
        if redir:
            return redir

        snapshot = await server._build_cloud_status_snapshot()
        if not snapshot.get("enrolled"):
          return JSONResponse(
            {
              "success": False,
              "detail": "This server is not enrolled with AutoYou Cloud. Use the link flow first.",
              "reregister_url": "/api/cloud/link-start",
            },
            status_code=409,
          )

        if snapshot.get("token_rejected"):
          return JSONResponse(
            {
              "success": False,
              "detail": "This cloud session was replaced or rejected. Complete the Cloud Pair link flow again.",
              "reregister_url": "/api/cloud/link-start",
            },
            status_code=409,
          )

        if snapshot.get("cloud_pair_entitlement_verified") and snapshot.get("cloud_pair_enabled"):
          block_reason = server._config_write_block_reason()
          if block_reason:
            return JSONResponse({"success": False, "detail": block_reason}, status_code=409)
          cfg = server._loaded_config_for_update()
          cloud_cfg = cfg.setdefault("cloud", {})
          cloud_cfg["pair_enabled"] = True
          cloud_cfg["pair_entitlement_verified"] = True
          server.STATE.config = server._save_and_reload_state_config(cfg)

        if snapshot.get("is_active") is False:
          return JSONResponse(await server._activate_current_cloud_server_registration())

        server.STATE.cloud_token_rejected = False
        server.asyncio.create_task(server._start_cloud_sse_listener())
        server.LOGGER.info("AutoYou Cloud: reregister triggered - restarting SSE listener.")
        return JSONResponse({
          "success": True,
          "message": "Cloud link reconnecting. If this server is not the active linked server, activate or re-link it from the Cloud card.",
        })

    @admin_app.get("/api/v1/x402/connect")
    async def x402_connect_info():
        """x402 discovery endpoint - returns payment requirements (402)."""
        return JSONResponse(
            status_code=402,
            content=server._x402_payment_requirements(),
            headers={"X-Payment-Required": "autoyou-cloud-subscription"},
        )

    @admin_app.post("/api/v1/x402/connect")
    async def x402_connect_verify(request: Request):
        """Verify an AutoYou Cloud subscription and issue a short-lived connection token.

        Request body (JSON)::

            {
              "auth_token": "<AutoYou Cloud auth token>",
              "server_token": "<optional: cloud server_token to verify server ownership>"
            }

        On success returns::

            {
              "access_token": "<short-lived token>",
              "expires_in": 300,
              "token_type": "x402",
              "server_id": "<this server's cloud server_id if registered>"
            }

        The ``access_token`` can be passed as ``Authorization: Bearer <access_token>``
        or in the ``X-Payment`` header on subsequent pairing API calls.
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="JSON body required.")

        guest_pass = str(body.get("guest_pass") or "").strip()
        if guest_pass:
            return await server._x402_connect_with_guest_pass(guest_pass)

        auth_token = str(body.get("auth_token") or "").strip()
        if not auth_token:
            return JSONResponse(
                status_code=402,
                content={**server._x402_payment_requirements(), "detail": "auth_token or guest_pass required"},
                headers={"X-Payment-Required": "autoyou-cloud-subscription"},
            )

        # Validate auth_token against AutoYou Cloud
        try:
            import httpx as _hx
            async with _hx.AsyncClient(timeout=10.0) as _cl:
                _resp = await _cl.get(
                    f"{server.AUTOYOU_CLOUD_BASE}/v1/account/me",
                    headers={"Authorization": f"Bearer {auth_token}"},
                )
            if _resp.status_code == 401:
                return JSONResponse(
                    status_code=402,
                    content={**server._x402_payment_requirements(), "detail": "Invalid or expired auth token"},
                    headers={"X-Payment-Required": "autoyou-cloud-subscription"},
                )
            if _resp.status_code == 402:
                return JSONResponse(
                    status_code=402,
                    content={**server._x402_payment_requirements(), "detail": "Active AutoYou Cloud subscription required to connect"},
                    headers={"X-Payment-Required": "autoyou-cloud-subscription"},
                )
            if _resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="Could not verify subscription with AutoYou Cloud.")
            acct = _resp.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"AutoYou Cloud unreachable: {exc}")

        user_id = str(acct.get("user_id") or acct.get("id") or "")
        email = str(acct.get("email") or "")

        # Verify the requesting user owns this server (optional but recommended)
        cloud_cfg = (server.STATE.config or {}).get("cloud", {})
        server_owner_id = cloud_cfg.get("user_id", "")
        if server_owner_id and user_id and server_owner_id != user_id:
            return JSONResponse(
                status_code=403,
                content={"detail": "This server is registered to a different account."},
            )

        # Issue short-lived access token
        access_token = server._x402_secrets.token_urlsafe(32)
        expires_at = server._x402_time.time() + server._X402_TOKEN_TTL
        server._X402_TOKENS[access_token] = {
            "user_id": user_id,
            "email": email,
            "expires_at": expires_at,
        }

        # Purge expired tokens opportunistically
        _now = server._x402_time.time()
        for _t in [k for k, v in list(server._X402_TOKENS.items()) if v["expires_at"] < _now]:
            server._X402_TOKENS.pop(_t, None)

        return JSONResponse({
            "access_token": access_token,
            "expires_in": server._X402_TOKEN_TTL,
            "token_type": "x402",
            "server_id": cloud_cfg.get("server_id", ""),
            "user_id": user_id,
            "email": email,
        })

    @admin_app.post("/api/v1/pair")
    async def x402_pair_endpoint(request: Request):
        """Process a pairing command authenticated by an x402 access token."""
        token_payload = server._validate_x402_token(request)
        if not token_payload:
            return JSONResponse(
                status_code=402,
                content={**server._x402_payment_requirements(), "detail": "Invalid or expired x402 connection token"},
                headers={"X-Payment-Required": "autoyou-cloud-subscription"},
            )

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="JSON body required.")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="JSON object required.")

        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="text field is required.")

        rate_limit_bucket, client_key = server._autopair_rate_limit_bucket(request)
        if not server.AUTOPAIR_RATE_LIMITER.is_allowed(rate_limit_bucket):
            server.LOGGER.warning("x402 pairing rate limit exceeded from %s", client_key)
            raise HTTPException(
                status_code=429,
                detail="Too many pairing attempts. Please wait a minute before trying again.",
            )

        if server.pairing_router is None:
            raise HTTPException(status_code=503, detail="Pairing subsystem not ready.")

        sender_id = str(token_payload.get("user_id") or "agent").strip() or "agent"
        try:
            server._configure_pairing_router_helpers()
            reply = await server.pairing_router.process_message(
                text.strip(),
                platform="x402-agent",
                sender_id=sender_id,
                identity_sender_id=sender_id,
            )
        except Exception as exc:
            server.LOGGER.exception("Error processing /api/v1/pair command: %s", exc)
            raise HTTPException(status_code=500, detail="Error processing pairing command.") from exc

        if reply == getattr(
            server.pairing_router,
            "FRAGMENT_CONSUMED",
            "__autopair_fragment_consumed__",
        ):
            raise HTTPException(status_code=400, detail="Incomplete pairing command.")

        if reply is None:
            raise HTTPException(status_code=400, detail="Not a recognized pairing command")

        response: dict[str, Any] = {"reply": str(reply)}
        otp_payload = server.parse_otp_response_payload(str(reply))
        if otp_payload is not None:
            response.update(otp_payload)

        return JSONResponse(response)

    @admin_app.get("/api/cloud/guest-access")
    async def cloud_guest_access_get(request: Request):
        """Admin: current x402 guest-access settings for this server."""
        redir = server._require_login(request)
        if redir:
            return redir
        cloud_cfg = (server.STATE.config or {}).get("cloud", {}) or {}
        return JSONResponse({
            "guest_access": server._x402_guest_access_config(),
            "server_id": str(cloud_cfg.get("server_id") or ""),
            "cloud_registered": bool(str(cloud_cfg.get("server_token") or "").strip()),
            "purchase_url": f"{server.AUTOYOU_CLOUD_BASE}/v1/x402/guest-pass",
        })

    @admin_app.post("/api/cloud/guest-access")
    async def cloud_guest_access_set(request: Request):
        """Admin: set x402 guest-access pricing and mirror it to AutoYou Cloud.

        Body: {"enabled": bool, "price_credits": float, "pass_ttl_seconds": int}
        """
        redir = server._require_login(request)
        if redir:
            return redir
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="JSON body required.")
        try:
            enabled = bool(body.get("enabled"))
            price_credits = round(max(0.0, float(body.get("price_credits") or 0.0)), 6)
            pass_ttl_seconds = int(min(max(float(body.get("pass_ttl_seconds") or 86400.0), 300.0), 30 * 24 * 3600.0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="enabled/price_credits/pass_ttl_seconds must be valid values.")
        if price_credits > 1000.0:
            raise HTTPException(status_code=400, detail="price_credits must be 1000 or less.")

        cfg = server.STATE.config or server._default_config()
        cloud_cfg = cfg.setdefault("cloud", {})
        guest_cfg = {
            "enabled": enabled,
            "price_credits": price_credits,
            "pass_ttl_seconds": pass_ttl_seconds,
        }
        cloud_cfg["guest_access"] = guest_cfg
        server._persist_state_config(cfg)
        server.STATE.config = cfg

        cloud_synced = False
        cloud_sync_error = ""
        server_token = str(cloud_cfg.get("server_token") or "").strip()
        server_id = str(cloud_cfg.get("server_id") or "").strip()
        if server_token and server_id:
            try:
                import httpx as _hx
                async with _hx.AsyncClient(timeout=10.0) as _cl:
                    _resp = await _cl.post(
                        f"{server.AUTOYOU_CLOUD_BASE}/v1/x402/guest-access",
                        headers={"Authorization": f"Bearer {server_token}"},
                        json={
                            "serverId": server_id,
                            "enabled": enabled,
                            "priceCredits": price_credits,
                            "passTtlSeconds": pass_ttl_seconds,
                            "displayName": server.get_configured_server_name() or "",
                        },
                    )
                cloud_synced = _resp.status_code < 400
                if not cloud_synced:
                    try:
                        cloud_sync_error = str(_resp.json().get("detail") or f"HTTP {_resp.status_code}")
                    except Exception:
                        cloud_sync_error = f"HTTP {_resp.status_code}"
            except Exception as exc:
                cloud_sync_error = str(exc)
        elif enabled:
            cloud_sync_error = "Server is not registered with AutoYou Cloud yet; link the server first."

        return JSONResponse({
            "guest_access": guest_cfg,
            "cloud_synced": cloud_synced,
            "cloud_sync_error": cloud_sync_error,
            "server_id": server_id,
        })

    return {
        "cloud_status": cloud_status,
        "cloud_ice_preview": cloud_ice_preview,
        "cloud_link_start": cloud_link_start,
        "cloud_callback": cloud_callback,
        "cloud_push_client": cloud_push_client,
        "cloud_notify_client": cloud_notify_client,
        "cloud_unregister": cloud_unregister,
        "cloud_activate": cloud_activate,
        "cloud_reregister": cloud_reregister,
        "x402_connect_info": x402_connect_info,
        "x402_connect_verify": x402_connect_verify,
        "x402_pair_endpoint": x402_pair_endpoint,
        "cloud_guest_access_get": cloud_guest_access_get,
        "cloud_guest_access_set": cloud_guest_access_set
    }
