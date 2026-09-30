# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-d578109c2fb80bd5eddd4fee

"""Admin Ui HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Any, Callable, Dict, Optional
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-d578109c2fb80bd5eddd4fee"


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    @admin_app.get("/ca.crt")
    @auth_app.get("/ca.crt")
    async def download_local_https_ca_certificate():
        """Serve the local HTTPS root CA so a client can install and trust it.

        A self-signed CA is never trusted automatically by any operating system, so
        HTTPS to this server is only authentic after the user installs this file and
        enables trust for it (Safari/macOS keychain, iOS profile + Certificate Trust
        Settings, or Android CA install). Served with the x509 CA content-type so iOS
        offers to install it directly.
        """
        try:
            from shared import local_tls
            pem = local_tls.ca_certificate_pem(server._CONFIG_DIR)
        except Exception as exc:  # pragma: no cover - defensive
            server.LOGGER.warning("Failed to read local HTTPS CA certificate: %s", exc)
            pem = None
        if not pem:
            return JSONResponse(
                status_code=404,
                content={"error": "Local HTTPS is not enabled on this server."},
            )
        return Response(
            content=pem,
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="autoyou-local-ca.crt"'},
        )

    @admin_app.get("/assets/logo.png")
    @auth_app.get("/assets/logo.png")
    async def admin_logo_asset():
        return await server._serve_admin_logo_file()

    @admin_app.get("/assets/logo.ico")
    @auth_app.get("/assets/logo.ico")
    async def admin_logo_ico_asset():
        return await server._serve_admin_icon_file()

    @admin_app.get("/favicon.ico")
    @auth_app.get("/favicon.ico")
    async def admin_favicon_asset():
        return await server._serve_admin_icon_file()

    @admin_app.get("/apple-touch-icon.png")
    @auth_app.get("/apple-touch-icon.png")
    async def admin_apple_touch_icon_asset():
        return await server._serve_admin_logo_file()

    @admin_app.get("/assets/admin/profile-image")
    async def admin_profile_image_asset(request: Request):
        # The operator's own photo. Its URL is only ever handed out in the
        # authenticated admin bootstrap payload, so requiring a session here
        # breaks nothing and keeps a personal image off an exposed instance.
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        return await server._serve_admin_profile_image_file()

    @admin_app.get("/api/admin/profile-image")
    async def admin_ui_get_profile_image(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        image_path = server._get_admin_profile_image_path()
        image_bytes = image_path.read_bytes() if image_path is not None and image_path.is_file() else b""
        if len(image_bytes) > server._ADMIN_PROFILE_IMAGE_MAX_BYTES:
            image_bytes = b""
        import base64
        return server._json_response_no_store({
            "profile_user_id": server._get_stable_server_id(),
            "has_photo": bool(image_bytes),
            "avatar_url": server._get_admin_profile_image_url() or "",
            "mime_type": server._get_admin_profile_image_media_type(image_path) if image_path and image_bytes else "",
            "data_base64": base64.b64encode(image_bytes).decode("ascii") if image_bytes else "",
        })

    @admin_app.post("/api/admin/profile-image")
    async def admin_ui_upload_profile_image(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error

        uploaded = None
        try:
            form = await request.form()
            uploaded = form.get("image")
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid upload form."})

        if uploaded is None or not hasattr(uploaded, "read"):
            return JSONResponse(status_code=400, content={"success": False, "error": "Profile image file is required."})

        try:
            image_payload = await uploaded.read(20 * 1024 * 1024)
            server._save_admin_profile_image(image_payload)
            if server.WEBRTC is not None:
                await server.WEBRTC.broadcast_server_profile()
            payload = await server._build_admin_ui_bootstrap_payload()
            return server._json_response_no_store(payload)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_ui_upload_profile_image failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
        finally:
            close_method = getattr(uploaded, "close", None)
            if callable(close_method):
                close_result = close_method()
                if server.asyncio.iscoroutine(close_result):
                    await close_result

    @admin_app.delete("/api/admin/profile-image")
    async def admin_ui_delete_profile_image(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error

        try:
            server._delete_admin_profile_image_files()
            if server.WEBRTC is not None:
                await server.WEBRTC.broadcast_server_profile()
            payload = await server._build_admin_ui_bootstrap_payload()
            return server._json_response_no_store(payload)
        except Exception as exc:
            server.LOGGER.error("admin_ui_delete_profile_image failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.get("/api/admin/desktop-assets")
    async def admin_ui_desktop_assets(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            from autoyou_agents.shared_tools.desktop_asset_store import get_desktop_asset_agent_catalog

            anchor = Path(__file__).resolve().parents[1] / "server.py"
            return server._json_response_no_store(get_desktop_asset_agent_catalog(anchor))
        except Exception as exc:
            server.LOGGER.error("admin_ui_desktop_assets failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.get("/api/admin/desktop-assets/{agent_name}/setup-prompt")
    async def admin_ui_desktop_asset_setup_prompt(agent_name: str, request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            from pathlib import Path
            from autoyou_agents.shared_tools.desktop_asset_store import (
                get_desktop_asset_agent_catalog,
                render_desktop_asset_setup_prompt,
            )

            anchor = Path(__file__).resolve().parents[1] / "server.py"
            catalog = get_desktop_asset_agent_catalog(anchor)
            if agent_name not in {item["agent_name"] for item in catalog["agents"]}:
                return JSONResponse(status_code=404, content={"success": False, "error": "Desktop agent is not available in this AutoYou build."})
            prompt = render_desktop_asset_setup_prompt(
                agent_name,
                anchor,
                platform=request.query_params.get("platform") or catalog["platform"],
                app_version=request.query_params.get("app_version") or "",
                theme=request.query_params.get("theme") or "auto",
                display_scale=request.query_params.get("display_scale") or "auto",
            )
            return server._json_response_no_store({"success": True, "agent_name": agent_name, "prompt": prompt})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_ui_desktop_asset_setup_prompt failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/admin/desktop-assets/{agent_name}/preferences")
    async def admin_ui_save_desktop_asset_preferences(agent_name: str, request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            from pathlib import Path
            from autoyou_agents.shared_tools.desktop_asset_store import (
                discover_packaged_desktop_agents,
                save_desktop_asset_preferences,
            )

            anchor = Path(__file__).resolve().parents[1] / "server.py"
            if agent_name not in discover_packaged_desktop_agents(anchor):
                return JSONResponse(status_code=404, content={"success": False, "error": "Desktop agent is not available in this AutoYou build."})
            payload = await request.json()
            preferences = save_desktop_asset_preferences(agent_name, payload)
            return server._json_response_no_store({"success": True, "agent_name": agent_name, "preferences": preferences})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_ui_save_desktop_asset_preferences failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.post("/api/admin/desktop-assets/{agent_name}/import")
    async def admin_ui_import_desktop_asset_pack(agent_name: str, request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        uploaded = None
        try:
            from pathlib import Path
            from autoyou_agents.shared_tools.desktop_asset_store import (
                MAX_BUNDLE_BYTES,
                discover_packaged_desktop_agents,
                get_packaged_desktop_agent_template,
                import_user_desktop_asset_bundle,
            )

            anchor = Path(__file__).resolve().parents[1] / "server.py"
            if agent_name not in discover_packaged_desktop_agents(anchor):
                return JSONResponse(status_code=404, content={"success": False, "error": "Desktop agent is not available in this AutoYou build."})
            form = await request.form()
            uploaded = form.get("bundle")
            if uploaded is None or not hasattr(uploaded, "read"):
                return JSONResponse(status_code=400, content={"success": False, "error": "Choose a desktop asset ZIP bundle."})
            payload = await uploaded.read(MAX_BUNDLE_BYTES + 1)
            result = import_user_desktop_asset_bundle(
                agent_name,
                payload,
                expected_app_id=str(get_packaged_desktop_agent_template(agent_name, anchor).get("app_id") or agent_name),
            )
            return server._json_response_no_store(result)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_ui_import_desktop_asset_pack failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
        finally:
            close_method = getattr(uploaded, "close", None)
            if callable(close_method):
                close_result = close_method()
                if server.asyncio.iscoroutine(close_result):
                    await close_result

    @admin_app.delete("/api/admin/desktop-assets/{agent_name}/{storage_id}")
    async def admin_ui_remove_desktop_asset_pack(agent_name: str, storage_id: str, request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        try:
            from pathlib import Path
            from autoyou_agents.shared_tools.desktop_asset_store import (
                discover_packaged_desktop_agents,
                remove_user_desktop_asset_pack,
            )

            anchor = Path(__file__).resolve().parents[1] / "server.py"
            if agent_name not in discover_packaged_desktop_agents(anchor):
                return JSONResponse(status_code=404, content={"success": False, "error": "Desktop agent is not available in this AutoYou build."})
            packs = remove_user_desktop_asset_pack(agent_name, storage_id)
            return server._json_response_no_store({"success": True, "agent_name": agent_name, "asset_packs": packs})
        except FileNotFoundError as exc:
            return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("admin_ui_remove_desktop_asset_pack failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    # The owner's photo on the admin origin. Viewers never need this route:
    # the Page site serves its own copy under /agent/page_agent/, gated by the
    # remote role policy, so here it is as private as /assets/admin/profile-image.
    @admin_app.get("/api/profile/avatar")
    async def page_agent_get_avatar(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        image_path = server._get_admin_profile_image_path()
        if image_path is None or not image_path.is_file():
            return JSONResponse(status_code=404, content={"error": "No profile photo"})
        try:
            image_bytes = image_path.read_bytes()
        except OSError:
            return JSONResponse(status_code=404, content={"error": "No profile photo"})
        media_type = server._get_admin_profile_image_media_type(image_path)
        return Response(
            content=image_bytes,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=86400" if request.query_params.get("v") else "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @admin_app.api_route("/api/profile/avatar", methods=["POST", "PUT", "PATCH"])
    async def page_agent_save_avatar(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        uploaded = None
        try:
            form = await request.form()
            uploaded = form.get("image")
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid image upload form."})

        if uploaded is None or not hasattr(uploaded, "read"):
            return JSONResponse(status_code=400, content={"success": False, "error": "Profile image file is required."})

        try:
            image_payload = await uploaded.read(20 * 1024 * 1024)
            image_path = server._save_admin_profile_image(image_payload)
            if server.WEBRTC is not None:
                await server.WEBRTC.broadcast_server_profile()
            import time
            try:
                version = int(image_path.stat().st_mtime_ns)
            except OSError:
                version = int(time.time() * 1_000_000_000)
            return JSONResponse(
                {
                    "success": True,
                    "has_photo": True,
                    "avatar_url": f"./api/profile/avatar?v={version}",
                },
                headers={"Cache-Control": "no-store"},
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
        except Exception as exc:
            server.LOGGER.error("page_agent_save_avatar failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
        finally:
            close_method = getattr(uploaded, "close", None)
            if callable(close_method):
                close_result = close_method()
                if server.asyncio.iscoroutine(close_result):
                    await close_result

    @admin_app.delete("/api/profile/avatar")
    async def page_agent_delete_avatar(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        try:
            server._delete_admin_profile_image_files()
            if server.WEBRTC is not None:
                await server.WEBRTC.broadcast_server_profile()
            return JSONResponse({"success": True, "has_photo": False}, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            server.LOGGER.error("page_agent_delete_avatar failed: %s", exc, exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

    @admin_app.get("/LICENSE", response_class=PlainTextResponse)
    @auth_app.get("/LICENSE", response_class=PlainTextResponse)
    async def get_license_file():
        return server._legal_file_response("LICENSE")

    @admin_app.get("/NOTICE.txt", response_class=PlainTextResponse)
    @auth_app.get("/NOTICE.txt", response_class=PlainTextResponse)
    async def get_notice_file():
        return server._legal_file_response("NOTICE.txt")

    @admin_app.get("/THIRD-PARTY-NOTICES.md", response_class=PlainTextResponse)
    @auth_app.get("/THIRD-PARTY-NOTICES.md", response_class=PlainTextResponse)
    async def get_third_party_notices_file():
        return server._legal_file_response("THIRD-PARTY-NOTICES.md")

    @admin_app.get("/sbom.cdx.json", response_class=PlainTextResponse)
    @auth_app.get("/sbom.cdx.json", response_class=PlainTextResponse)
    async def get_sbom_file():
        return server._legal_file_response("sbom.cdx.json")

    @admin_app.get("/api/local-pair-helper", response_class=HTMLResponse)
    async def local_pair_helper(request: Request):
        client_host = request.client.host if request.client else None
        is_allowed = (
            server._is_loopback_client_host(client_host)
            or str(request.url.scheme or "").lower() == "https"
            or server._csrf_peer_is_private_or_loopback(client_host or "")
        )
        if not is_allowed:
            return HTMLResponse(
                "This endpoint requires localhost, HTTPS, or a private-network Local Pair client.",
                status_code=403,
            )
        return HTMLResponse(
            """<!doctype html><meta charset="utf-8"><meta name="autoyou-local-pair-helper" content="1"><title>AutoYou Local Pair</title>""",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @admin_app.get("/login", response_class=HTMLResponse)
    async def admin_login_page(request: Request):
        transport_error = server._require_loopback_or_https_request(request, allow_local_pair=True)
        if transport_error:
            return HTMLResponse(
                "This endpoint requires localhost or HTTPS.",
                status_code=transport_error.status_code,
            )
        # If already logged in via cookie, go straight to dashboard
        if server._is_logged_in(request):
            return RedirectResponse(url="/", status_code=302)

        first_run = not server._saved_config_exists()
        default_password_setup = bool(getattr(server.STATE, "used_default_password", False))
        setup_password_mode = bool(first_run or default_password_setup)
        agreement_pending_unlock = bool(
            not first_run
            and server.is_license_acknowledgement_pending_unlock(anchor=server.__file__)
        )
        legal_acceptance_required = bool(
            first_run
            or (
                not agreement_pending_unlock
                and not server.is_license_acknowledged(anchor=server.__file__)
            )
        )
        server_name = (server.STATE.config.get("server", {}).get("name") or "AutoYou-Server") if server.STATE.config else "AutoYou-Server"
        server_name_display = server.html.escape(server_name)
        if default_password_setup:
            login_title = f"Finish setup for {server_name_display}"
            login_tagline = (
                "Start with the default settings, then sign in to AutoYou Cloud on this computer "
                "and your phone. You can also choose your own server password."
            )
            password_label = "Server password"
            password_placeholder = "Generate a new password or enter the bootstrap default"
            submit_label = "Start"
        elif first_run:
            login_title = f"Set up {server_name_display}"
            login_tagline = "Choose a server password to encrypt this AutoYou install."
            password_label = "Server password to start"
            password_placeholder = "Generate or enter a server password"
            submit_label = "Start"
        else:
            login_title = f"Unlock {server_name_display}"
            login_tagline = "Enter your server password to unlock this AutoYou install."
            password_label = "Server password"
            password_placeholder = "Enter server password"
            submit_label = "Unlock"
        startup_headline = "Loading encrypted configuration to start" if first_run else "Loading encrypted configuration"
        startup_detail = (
            "Loading the encrypted runtime and starting services."
            if first_run else
            "Loading the encrypted runtime."
        )
        login_failure_message = (
            "Could not start the encrypted configuration. Check the password and try again."
            if first_run else
            "Could not decrypt the configuration. Check the password and try again."
        )
        # Default-password notice - shown CONSISTENTLY on both the first-run "Set up"
        # page and subsequent "Unlock" pages. Previously it only rendered once the
        # config existed (used_default_password), so a fresh first run showed no
        # warning while the very next run did - confusing on a clean install.
        if default_password_setup:
            warn_default = (
                "<div class='ayu-login-chip ayu-login-chip-warn'>Default password "
                "<code>autoyou123</code> is active. Secure Cloud Pair works with the same paid account "
                "on this computer and the current phone app. Public links require a custom password. "
                "If you change the password, use Set up your phone after unlock to scan the matching settings.</div>"
            )
        elif first_run:
            warn_default = (
                "<div class='ayu-login-chip ayu-login-chip-warn'>First run - the bootstrap default "
                "password is <code>autoyou123</code>. Generate or type your own strong password "
                "to set it now. Then scan the phone setup QR from Overview so your mobile password matches.</div>"
            )
        else:
            warn_default = ""
        login_brand_html = server._build_admin_brand_markup(server_name)
        native_credential_name = (
            "macOS Keychain"
            if server.sys.platform == "darwin"
            else "Windows Credential Manager"
            if server.sys.platform == "win32"
            else "system keyring"
        )
        agreement_pending_html = (
            "<p>Your existing agreement will be verified after the protected local data is unlocked.</p>"
            if agreement_pending_unlock
            else ""
        )
        native_unlock_available = bool(
            (not first_run)
            and not legal_acceptance_required
            and server._native_unlock_enabled()
            and server._keystore_server_password_available()
        )
        native_unlock_html = (
            f"""
              <div class='ayu-native-unlock'>
                <button id='native-unlock' class='ayu-login-secondary' type='button'>
                  <svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' d='M16.5 10.5V7.5a4.5 4.5 0 0 0-9 0v3m-.75 0h10.5A1.75 1.75 0 0 1 19 12.25v6A1.75 1.75 0 0 1 17.25 20H6.75A1.75 1.75 0 0 1 5 18.25v-6a1.75 1.75 0 0 1 1.75-1.75Z'/></svg>
                  <span>Unlock with {server.html.escape(native_credential_name)}</span>
                </button>
                <p>The password stays hidden. Your computer may ask permission to release the saved AutoYou password.</p>
                {agreement_pending_html}
              </div>
            """
            if native_unlock_available
            else ""
        )
        legal_acceptance_html = (
            """
              <label class='ayu-legal-acceptance' for='terms-accepted'>
                <input id='terms-accepted' type='checkbox' name='terms_accepted' value='1' required>
                <span>I accept the current Terms of Use (EULA), License, responsibility terms, warranty disclaimer, and liability limits. I understand that this runtime acceptance permits personal, non-commercial use only; Enterprise Use requires a separate written agreement with OpenStorey.</span>
              </label>
            """
            if legal_acceptance_required
            else ""
        )
        reset_panel_html = """
              <div class='ayu-reset-panel'>
                <div class='ayu-reset-illustration' aria-hidden='true'>
                  <svg viewBox='0 0 120 88' role='img'>
                    <rect x='20' y='18' width='80' height='48' rx='9' fill='currentColor' opacity='.10'/>
                    <rect x='30' y='28' width='60' height='28' rx='6' fill='currentColor' opacity='.18'/>
                    <path d='M42 42h36M47 50h26' stroke='currentColor' stroke-width='4' stroke-linecap='round' opacity='.46'/>
                    <path d='M84 22l12-12M92 22l8-8M78 66l-10 12M86 66l-14 14' stroke='currentColor' stroke-width='4' stroke-linecap='round'/>
                    <circle cx='60' cy='42' r='37' fill='none' stroke='currentColor' stroke-width='4' opacity='.28'/>
                  </svg>
                </div>
                <div>
                  <strong>Start over on this computer</strong>
                  <p>Reset removes local AutoYou settings, sessions, saved pairing details, agent registries, and stored credential entries. The app then shuts down so the next launch is a fresh setup.</p>
                </div>
                <button id='open-reset-modal' class='ayu-reset-link' type='button'>Reset</button>
              </div>
        """

        body = f"""
        <style>
        #ayu-login-root {{
          --ayu-bg-start:#faf9ff; --ayu-bg:#f5f6fa; --ayu-bg-end:#eef2ff;
          --ayu-bg-accent:rgba(124,58,237,.14); --ayu-bg-accent-soft:rgba(59,130,246,.12);
          --ayu-surface:rgba(255,255,255,.86); --ayu-surface-strong:#ffffff; --ayu-surface-muted:rgba(248,250,252,.92);
          --ayu-border:rgba(143,155,179,.25); --ayu-border-strong:rgba(124,58,237,.22);
          --ayu-text:#19162a; --ayu-muted:#615d78; --ayu-faint:#8a88a2;
          --ayu-primary:#6d28d9; --ayu-primary-strong:#4c1d95;
          --ayu-amber:#b45309; --ayu-amber-soft:#fff7ed; --ayu-red:#b91c1c;
          --ayu-shadow:0 30px 80px rgba(39,31,82,.12); --ayu-shadow-soft:0 14px 40px rgba(39,31,82,.08);
          --ayu-modal-backdrop:rgba(17,24,39,.54);
          --ayu-font-body:"Avenir Next","Segoe UI","Helvetica Neue",sans-serif;
          --ayu-font-display:"Optima","Avenir Next",sans-serif;
          --ayu-font-mono:"SFMono-Regular","SF Mono",Menlo,Monaco,Consolas,monospace;
        }}
        :root[data-theme="dark"] #ayu-login-root {{
          --ayu-bg-start:#040814; --ayu-bg:#08101d; --ayu-bg-end:#0a1224;
          --ayu-bg-accent:rgba(59,130,246,.15); --ayu-bg-accent-soft:rgba(124,58,237,.18);
          --ayu-surface:rgba(10,16,31,.86); --ayu-surface-strong:#0f172a; --ayu-surface-muted:rgba(15,23,42,.94);
          --ayu-border:rgba(143,155,179,.18); --ayu-border-strong:rgba(96,165,250,.24);
          --ayu-text:#eef2ff; --ayu-muted:#bac5dd; --ayu-faint:#91a3c4;
          --ayu-primary:#8b5cf6; --ayu-primary-strong:#ddd6fe;
          --ayu-amber:#fdba74; --ayu-amber-soft:rgba(245,158,11,.16); --ayu-red:#fca5a5;
          --ayu-shadow:0 32px 90px rgba(2,6,23,.5); --ayu-shadow-soft:0 18px 48px rgba(2,6,23,.34);
          --ayu-modal-backdrop:rgba(2,6,23,.72);
        }}
        body:has(#ayu-login-root) {{
          background:
            radial-gradient(circle at top left, var(--ayu-bg-accent), transparent 34%),
            radial-gradient(circle at right 18%, var(--ayu-bg-accent-soft), transparent 28%),
            linear-gradient(180deg, var(--ayu-bg-start) 0%, var(--ayu-bg) 42%, var(--ayu-bg-end) 100%) !important;
        }}
        body:has(#ayu-login-root) .container {{ max-width:none !important; margin:0 !important; padding:0 !important; width:100% !important; }}
                #ayu-login-root {{ min-height:100vh; display:flex; align-items:center; justify-content:center; padding:clamp(18px,3vw,32px); box-sizing:border-box; color:var(--ayu-text); font-family:var(--ayu-font-body); }}
                #ayu-login-root *, #ayu-login-root *:before, #ayu-login-root *:after {{ box-sizing:border-box; }}
                #ayu-login-root .ayu-login-card {{ width:min(940px,100%); background:var(--ayu-surface); backdrop-filter:blur(18px); border:1px solid var(--ayu-border); border-radius:24px; box-shadow:var(--ayu-shadow); padding:0; display:grid; grid-template-columns:minmax(0,.95fr) minmax(360px,440px); gap:0; overflow:hidden; }}
                #ayu-login-root .ayu-login-intro {{ min-height:520px; padding:32px; display:grid; align-content:space-between; gap:24px; border-right:1px solid var(--ayu-border); background:linear-gradient(145deg,rgba(255,255,255,.72),rgba(236,253,245,.44)); }}
                :root[data-theme="dark"] #ayu-login-root .ayu-login-intro {{ background:linear-gradient(145deg,rgba(15,23,42,.76),rgba(20,83,45,.16)); }}
                #ayu-login-root .ayu-login-main {{ padding:32px; display:grid; gap:14px; align-content:center; }}
                #ayu-login-root .ayu-login-brand {{ display:flex; align-items:center; gap:14px; }}
                #ayu-login-root .brand-lockup {{ display:flex; align-items:center; gap:13px; }}
                #ayu-login-root .brand-logo {{ width:42px; height:42px; object-fit:contain; }}
                #ayu-login-root .brand-meta {{ display:flex; flex-direction:column; line-height:1.2; }}
                #ayu-login-root .brand-kicker {{ font-size:11px; letter-spacing:0; text-transform:uppercase; color:var(--ayu-faint); font-weight:700; }}
                #ayu-login-root .brand-name {{ font-family:var(--ayu-font-display); font-size:16px; font-weight:700; color:var(--ayu-text); }}
                #ayu-login-root .ayu-login-eyebrow {{ font-size:11px; font-weight:800; letter-spacing:0; text-transform:uppercase; color:var(--ayu-faint); }}
                #ayu-login-root .ayu-login-title {{ margin:6px 0 0; font-family:var(--ayu-font-display); font-size:32px; line-height:1.08; letter-spacing:0; color:var(--ayu-text); }}
                #ayu-login-root .ayu-login-tagline {{ margin:8px 0 0; color:var(--ayu-muted); font-size:14px; line-height:1.55; }}
                #ayu-login-root .ayu-login-illustration {{ color:var(--ayu-primary); min-height:180px; display:grid; place-items:center; }}
                #ayu-login-root .ayu-login-illustration svg {{ width:min(260px,80%); height:auto; filter:drop-shadow(0 20px 38px rgba(39,31,82,.16)); }}
                #ayu-login-root .ayu-login-chip {{ display:flex; align-items:flex-start; gap:8px; font-size:12.5px; line-height:1.5; padding:11px 13px; border-radius:14px; }}
        #ayu-login-root .ayu-login-chip code {{ font-family:var(--ayu-font-mono); font-size:.92em; }}
        #ayu-login-root .ayu-login-chip-warn {{ background:var(--ayu-amber-soft); color:var(--ayu-amber); border:1px solid rgba(180,83,9,.22); }}
        #ayu-login-root form {{ display:grid; gap:9px; }}
        #ayu-login-root label {{ font-size:12px; font-weight:700; color:var(--ayu-text); }}
        #ayu-login-root .ayu-login-input {{ width:100%; border:1px solid var(--ayu-border); background:var(--ayu-surface-strong); color:var(--ayu-text); border-radius:14px; padding:13px 15px; font:inherit; transition:border-color .18s ease, box-shadow .18s ease; }}
        #ayu-login-root .ayu-login-input:focus {{ outline:none; border-color:rgba(109,40,217,.45); box-shadow:0 0 0 4px rgba(109,40,217,.12); }}
        #ayu-login-root .ayu-login-password-row {{ display:grid; grid-template-columns:1fr auto; gap:8px; align-items:center; }}
        #ayu-login-root .ayu-login-icon-btn {{ width:44px; height:44px; border-radius:14px; border:1px solid var(--ayu-border); background:var(--ayu-surface-strong); color:var(--ayu-muted); display:grid; place-items:center; cursor:pointer; transition:transform .18s ease, border-color .18s ease, color .18s ease; }}
        #ayu-login-root .ayu-login-icon-btn:hover, #ayu-login-root .ayu-login-icon-btn:focus-visible {{ transform:translateY(-1px); border-color:var(--ayu-border-strong); color:var(--ayu-text); outline:none; }}
        #ayu-login-root .ayu-login-icon-btn svg {{ width:18px; height:18px; }}
        #ayu-login-root .ayu-login-password-tools {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
        #ayu-login-root .ayu-login-tool-btn {{ appearance:none; border:1px solid var(--ayu-border); border-radius:999px; padding:9px 12px; cursor:pointer; font:inherit; font-size:12px; font-weight:700; color:var(--ayu-text); background:var(--ayu-surface-strong); display:inline-flex; align-items:center; gap:7px; transition:transform .18s ease, border-color .18s ease; }}
        #ayu-login-root .ayu-login-tool-btn:hover, #ayu-login-root .ayu-login-tool-btn:focus-visible {{ transform:translateY(-1px); border-color:var(--ayu-border-strong); outline:none; }}
        #ayu-login-root .ayu-login-tool-btn svg {{ width:15px; height:15px; }}
                #ayu-login-root .ayu-login-submit {{ appearance:none; border:0; margin-top:5px; border-radius:999px; padding:13px 18px; cursor:pointer; font:inherit; font-weight:700; font-size:14px; color:#fff; background:linear-gradient(135deg,#6d28d9,#4f46e5); box-shadow:0 14px 30px rgba(79,70,229,.22); transition:transform .18s ease, box-shadow .18s ease, opacity .18s ease; }}
                #ayu-login-root .ayu-login-submit:hover {{ transform:translateY(-1px); box-shadow:0 18px 36px rgba(79,70,229,.3); }}
                #ayu-login-root .ayu-login-submit:disabled {{ cursor:wait; opacity:.7; transform:none; box-shadow:none; }}
                #ayu-login-root .ayu-login-secondary {{ width:100%; appearance:none; border:1px solid var(--ayu-border); border-radius:999px; padding:12px 16px; cursor:pointer; font:inherit; font-weight:800; font-size:13px; color:var(--ayu-text); background:var(--ayu-surface-strong); display:flex; justify-content:center; align-items:center; gap:8px; transition:transform .18s ease, border-color .18s ease, opacity .18s ease; }}
                #ayu-login-root .ayu-login-secondary:hover, #ayu-login-root .ayu-login-secondary:focus-visible {{ transform:translateY(-1px); border-color:var(--ayu-border-strong); outline:none; }}
                #ayu-login-root .ayu-login-secondary:disabled {{ cursor:wait; opacity:.7; transform:none; }}
                #ayu-login-root .ayu-login-secondary svg {{ width:16px; height:16px; }}
                #ayu-login-root .ayu-native-unlock {{ display:grid; gap:7px; }}
                #ayu-login-root .ayu-native-unlock p {{ margin:0; font-size:12px; line-height:1.45; color:var(--ayu-muted); }}
                #ayu-login-root .ayu-login-error {{ font-size:13px; line-height:1.5; padding:11px 13px; border-radius:14px; background:rgba(185,28,28,.1); color:var(--ayu-red); border:1px solid rgba(185,28,28,.2); }}
                #ayu-login-root .ayu-login-error.hidden {{ display:none; }}
                #ayu-login-root .ayu-reset-panel {{ display:grid; grid-template-columns:auto 1fr auto; gap:12px; align-items:center; padding:13px; border:1px solid var(--ayu-border); border-radius:16px; background:var(--ayu-surface-muted); }}
                #ayu-login-root .ayu-reset-illustration {{ width:54px; height:44px; color:var(--ayu-red); display:grid; place-items:center; }}
                #ayu-login-root .ayu-reset-illustration svg {{ width:54px; height:44px; }}
                #ayu-login-root .ayu-reset-panel strong {{ display:block; font-size:12.5px; color:var(--ayu-text); }}
                #ayu-login-root .ayu-reset-panel p {{ margin:3px 0 0; font-size:11.5px; line-height:1.4; color:var(--ayu-muted); }}
                #ayu-login-root .ayu-reset-link {{ appearance:none; border:1px solid rgba(185,28,28,.26); border-radius:999px; padding:8px 12px; cursor:pointer; font:inherit; font-size:12px; font-weight:800; color:var(--ayu-red); background:rgba(185,28,28,.08); }}
                #ayu-login-root .ayu-reset-link:hover, #ayu-login-root .ayu-reset-link:focus-visible {{ outline:none; border-color:rgba(185,28,28,.46); }}
                #ayu-login-root .ayu-reset-modal {{ position:fixed; inset:0; z-index:1500; display:flex; align-items:center; justify-content:center; padding:24px; background:var(--ayu-modal-backdrop); backdrop-filter:blur(10px); }}
                #ayu-login-root .ayu-reset-modal.hidden {{ display:none; }}
                #ayu-login-root .ayu-reset-modal-panel {{ width:min(480px,100%); background:var(--ayu-surface-strong); border:1px solid var(--ayu-border); border-radius:22px; box-shadow:var(--ayu-shadow); padding:24px; display:grid; gap:16px; }}
                #ayu-login-root .ayu-reset-modal-head {{ display:grid; grid-template-columns:auto 1fr; gap:14px; align-items:center; }}
                #ayu-login-root .ayu-reset-modal-icon {{ width:52px; height:52px; border-radius:16px; display:grid; place-items:center; color:var(--ayu-red); background:rgba(185,28,28,.08); }}
                #ayu-login-root .ayu-reset-modal-icon svg {{ width:28px; height:28px; }}
                #ayu-login-root .ayu-reset-modal h2 {{ margin:0; font-family:var(--ayu-font-display); font-size:22px; letter-spacing:0; }}
                #ayu-login-root .ayu-reset-modal p {{ margin:5px 0 0; color:var(--ayu-muted); font-size:13px; line-height:1.5; }}
                #ayu-login-root .ayu-reset-modal ul {{ margin:0; padding-left:18px; color:var(--ayu-muted); font-size:12.5px; line-height:1.55; }}
                #ayu-login-root .ayu-reset-modal-actions {{ display:flex; flex-wrap:wrap; justify-content:flex-end; gap:9px; }}
                #ayu-login-root .ayu-reset-cancel, #ayu-login-root .ayu-reset-confirm {{ appearance:none; border-radius:999px; padding:11px 15px; cursor:pointer; font:inherit; font-size:13px; font-weight:800; }}
                #ayu-login-root .ayu-reset-cancel {{ border:1px solid var(--ayu-border); background:var(--ayu-surface-muted); color:var(--ayu-text); }}
                #ayu-login-root .ayu-reset-confirm {{ border:0; background:var(--ayu-red); color:#fff; }}
                #ayu-login-root .ayu-reset-confirm:disabled {{ cursor:wait; opacity:.72; }}
                #ayu-login-root .startup-overlay {{ position:fixed; inset:0; z-index:1400; display:flex; align-items:center; justify-content:center; padding:24px; background:var(--ayu-modal-backdrop); backdrop-filter:blur(10px); }}
        #ayu-login-root .startup-overlay.hidden {{ display:none; }}
        #ayu-login-root .startup-panel {{ width:min(460px,100%); max-height:min(92vh,860px); overflow:auto; background:var(--ayu-surface-strong); border:1px solid var(--ayu-border); border-radius:28px; padding:30px; box-shadow:var(--ayu-shadow); display:grid; gap:20px; }}
        #ayu-login-root .startup-headline {{ margin:10px 0 0; font-family:var(--ayu-font-display); font-size:22px; font-weight:700; color:var(--ayu-text); }}
        #ayu-login-root .startup-detail {{ margin:6px 0 0; color:var(--ayu-muted); font-size:13.5px; line-height:1.55; }}
        #ayu-login-root .progress-track {{ width:100%; height:8px; border-radius:999px; background:rgba(143,155,179,.18); overflow:hidden; }}
        #ayu-login-root .progress-fill {{ height:100%; width:10%; border-radius:inherit; background:linear-gradient(135deg,#6d28d9,#2563eb); transition:width .35s ease; }}
        #ayu-login-root .startup-meta {{ display:flex; align-items:center; justify-content:space-between; font-size:12px; color:var(--ayu-faint); font-weight:600; }}
        #ayu-login-root .boot-sweep-card {{ background:var(--ayu-surface-muted); border:1px solid var(--ayu-border); border-radius:20px; padding:18px; display:flex; flex-direction:column; gap:11px; }}
                #ayu-login-root .boot-sweep-card .eyebrow {{ font-size:11px; font-weight:800; letter-spacing:0; text-transform:uppercase; color:var(--ayu-faint); }}
        #ayu-login-root .boot-sweep-card h3 {{ margin:2px 0 0; font-family:var(--ayu-font-display); font-size:16px; font-weight:700; color:var(--ayu-text); }}
        #ayu-login-root .boot-sweep-copy {{ margin:0; color:var(--ayu-muted); font-size:12.5px; line-height:1.5; }}
        #ayu-login-root .boot-sweep-board {{ position:relative; height:200px; border-radius:16px; overflow:hidden; border:1px solid var(--ayu-border); background:linear-gradient(180deg,var(--ayu-surface-strong),var(--ayu-surface-muted)); }}
        #ayu-login-root .boot-sweep-board:before {{ content:""; position:absolute; inset:0; background-image:linear-gradient(rgba(143,155,179,.08) 1px,transparent 1px),linear-gradient(90deg,rgba(143,155,179,.08) 1px,transparent 1px); background-size:24px 24px; pointer-events:none; }}
        #ayu-login-root .boot-sweep-node {{ position:absolute; width:18px; height:18px; min-width:0; min-height:0; line-height:0; border:0; padding:0; margin:0; border-radius:999px; background:radial-gradient(circle at 32% 30%,#ddd6fe,#8b5cf6 60%,#6d28d9 100%); box-shadow:0 6px 14px rgba(109,40,217,.28); cursor:pointer; transform:translate(-50%,-50%); animation:ayuLoginSweep 1.5s ease-in-out infinite; touch-action:manipulation; }}
        #ayu-login-root .boot-sweep-node:after {{ content:""; position:absolute; inset:-7px; border-radius:999px; border:1px solid rgba(109,40,217,.28); }}
        @keyframes ayuLoginSweep {{ 0%,100% {{ transform:translate(-50%,-50%) scale(1); }} 50% {{ transform:translate(-50%,-50%) scale(1.12); }} }}
        #ayu-login-root .boot-sweep-score {{ display:flex; gap:10px; }}
        #ayu-login-root .score-pill {{ flex:1; background:var(--ayu-surface-strong); border:1px solid var(--ayu-border); border-radius:14px; padding:11px 13px; display:flex; flex-direction:column; gap:2px; }}
        #ayu-login-root .score-pill strong {{ font-size:20px; font-weight:800; color:var(--ayu-text); font-family:var(--ayu-font-display); }}
        #ayu-login-root .score-pill span {{ font-size:11px; color:var(--ayu-faint); }}
        #ayu-login-root .boot-sweep-note {{ font-size:11.5px; color:var(--ayu-faint); line-height:1.5; }}
                @media (max-width:820px) {{ #ayu-login-root .ayu-login-card {{ width:min(460px,100%); grid-template-columns:1fr; }} #ayu-login-root .ayu-login-intro {{ min-height:0; border-right:0; border-bottom:1px solid var(--ayu-border); padding:24px 24px 18px; }} #ayu-login-root .ayu-login-illustration {{ display:none; }} #ayu-login-root .ayu-login-main {{ padding:24px; }} }}
                @media (max-width:560px) {{ #ayu-login-root {{ padding:14px; }} #ayu-login-root .ayu-login-title {{ font-size:28px; }} #ayu-login-root .startup-panel {{ padding:24px; }} #ayu-login-root .ayu-reset-panel {{ grid-template-columns:1fr auto; }} #ayu-login-root .ayu-reset-illustration {{ display:none; }} }}
        @media (prefers-reduced-motion:reduce) {{ #ayu-login-root .boot-sweep-node {{ animation:none; }} #ayu-login-root .progress-fill {{ transition:none; }} }}
                </style>
                <div id="ayu-login-root">
                  <div class='ayu-login-card'>
                    <section class='ayu-login-intro'>
                      <div>
                        <div class='ayu-login-brand'>{login_brand_html}</div>
                        <div>
                          <div class='ayu-login-eyebrow'>Secure boot</div>
                          <h1 class='ayu-login-title'>{login_title}</h1>
                          <p class='ayu-login-tagline'>{login_tagline}</p>
                        </div>
                        {warn_default}
                      </div>
                      <div class='ayu-login-illustration' aria-hidden='true'>
                        <svg viewBox='0 0 260 200' role='img'>
                          <rect x='44' y='44' width='172' height='104' rx='22' fill='currentColor' opacity='.10'/>
                          <rect x='64' y='64' width='132' height='66' rx='14' fill='currentColor' opacity='.16'/>
                          <path d='M92 96h76M108 116h44' stroke='currentColor' stroke-width='8' stroke-linecap='round' opacity='.38'/>
                          <path d='M130 58V42a30 30 0 0 1 60 0v16' fill='none' stroke='currentColor' stroke-width='10' stroke-linecap='round' opacity='.62'/>
                          <circle cx='130' cy='97' r='58' fill='none' stroke='currentColor' stroke-width='8' opacity='.22'/>
                          <path d='M188 146l28 28M216 146l-28 28' stroke='currentColor' stroke-width='8' stroke-linecap='round'/>
                        </svg>
                      </div>
                    </section>
                    <section class='ayu-login-main'>
                      <form id='login-form' method='post' action='/login'>
                        <input id='unused-server-username' type='text' name='username' value='admin' autocomplete='username' style='display:none' aria-hidden='true' tabindex='-1'>
                        <label for='server-password'>{password_label}</label>
                        <div class='ayu-login-password-row'>
                          <input id='server-password' class='ayu-login-input' type='password' name='password' placeholder='{password_placeholder}' autocomplete='{"new-password" if setup_password_mode else "current-password"}' required>
                          <button id='toggle-password' class='ayu-login-icon-btn' type='button' aria-label='Show password' title='Show password'>
                            <svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' d='M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.964-7.178Z'/><path stroke-linecap='round' stroke-linejoin='round' d='M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'/></svg>
                          </button>
                        </div>
                        {"<div class='ayu-login-password-tools'><button id='generate-password' class='ayu-login-tool-btn' type='button'><svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' d='M15.59 14.37a6 6 0 1 1-5.84-10.38m5.84 10.38 4.13 4.13m-4.13-4.13 4.13-4.13M12 8v4l2.5 1.5'/></svg><span>Generate random password</span></button><button id='copy-password' class='ayu-login-tool-btn' type='button'><svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' d='M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2m-6 12h8a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2Z'/></svg><span>Copy</span></button></div>" if setup_password_mode else ""}
              <style>
                #ayu-legal-disclosure {{ margin-top:10px; border-radius:16px; overflow:hidden; border:1px solid var(--ayu-border); background:var(--ayu-surface-muted); }}
                #ayu-legal-disclosure .ayu-legal-bar {{ display:flex; align-items:center; gap:10px; padding:12px 15px; cursor:pointer; user-select:none; -webkit-user-select:none; transition:background .18s ease; }}
                #ayu-legal-disclosure .ayu-legal-bar:hover {{ background:var(--ayu-bg-accent); }}
                #ayu-legal-disclosure .ayu-legal-icon {{ width:28px; height:28px; border-radius:8px; background:linear-gradient(135deg,rgba(109,40,217,.14),rgba(59,130,246,.10)); display:grid; place-items:center; flex-shrink:0; }}
                #ayu-legal-disclosure .ayu-legal-icon svg {{ width:14px; height:14px; color:var(--ayu-primary); }}
                #ayu-legal-disclosure .ayu-legal-summary {{ flex:1; font-size:12.5px; color:var(--ayu-text); line-height:1.4; }}
                #ayu-legal-disclosure .ayu-legal-summary a {{ color:var(--ayu-primary); font-weight:700; text-decoration:none; border-bottom:1px dashed currentColor; }}
                #ayu-legal-disclosure .ayu-legal-summary a:hover {{ border-bottom-style:solid; }}
                #ayu-legal-disclosure .ayu-legal-chevron {{ width:18px; height:18px; flex-shrink:0; color:var(--ayu-faint); transition:transform .25s ease; }}
                #ayu-legal-disclosure.open .ayu-legal-chevron {{ transform:rotate(180deg); }}
                #ayu-legal-disclosure .ayu-legal-body {{ max-height:0; overflow:hidden; transition:max-height .35s cubic-bezier(.4,0,.2,1); }}
                #ayu-legal-disclosure.open .ayu-legal-body {{ max-height:280px; }}
                #ayu-legal-disclosure .ayu-legal-content {{ padding:0 15px 14px; font-size:11px; line-height:1.55; color:var(--ayu-muted); text-align:justify; border-top:1px solid var(--ayu-border); margin-top:0; padding-top:12px; }}
                        #ayu-legal-disclosure .ayu-legal-content strong {{ color:var(--ayu-text); font-weight:700; letter-spacing:0; }}
                #ayu-login-root .ayu-legal-acceptance {{ display:flex; gap:10px; align-items:center; margin-top:10px; padding:12px 13px; border-radius:14px; border:1px solid var(--ayu-border); background:var(--ayu-surface-muted); font-size:12px; line-height:1.45; color:var(--ayu-text); cursor:pointer; }}
                #ayu-login-root .ayu-legal-acceptance input[type='checkbox'] {{ appearance:auto; width:18px; min-width:18px; max-width:18px; height:18px; min-height:18px; max-height:18px; flex:0 0 18px; margin:0; padding:0; cursor:pointer; accent-color:var(--ayu-primary); }}
              </style>
              <div id='ayu-legal-disclosure'>
                <div class='ayu-legal-bar' onclick="this.parentElement.classList.toggle('open')" role='button' tabindex='0' aria-expanded='false' aria-controls='ayu-legal-body'>
                  <span class='ayu-legal-icon'><svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' d='M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z'/></svg></span>
                  <span class='ayu-legal-summary'>By continuing you agree to the <a href='https://www.autoyou.me/terms/' target='_blank' rel='noreferrer'>Terms&nbsp;of&nbsp;Use&nbsp;(EULA)</a>, <a href='/LICENSE' target='_blank'>License&nbsp;Terms</a>, <a href='https://www.autoyou.me/privacy/' target='_blank' rel='noreferrer'>Privacy&nbsp;Policy</a>, user responsibility, warranty disclaimer, and liability limits.</span>
                  <svg class='ayu-legal-chevron' xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2.5' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' d='m19.5 8.25-7.5 7.5-7.5-7.5'/></svg>
                </div>
                <div class='ayu-legal-body' id='ayu-legal-body'>
                  <div class='ayu-legal-content'>
                    <strong>RESPONSIBILITY NOTICE:</strong> You are responsible for the data, accounts, prompts, models, automations, messages, third-party services, and operations you connect to AutoYou. Use AutoYou only with lawful authority, required consents, and compliance with applicable privacy, telecom, platform, export, intellectual-property, and consumer-protection rules. AutoYou is provided as-is; OpenStorey LLC, the AutoYou owner board, and contributors disclaim warranties and limit liability to the maximum extent permitted by law. Bundled release notices are available at <a href='/NOTICE.txt' target='_blank'>NOTICE</a>, <a href='/THIRD-PARTY-NOTICES.md' target='_blank'>Third-Party Notices</a>, and <a href='/sbom.cdx.json' target='_blank'>SBOM</a>. If you do not agree, do not use this software.
                  </div>
                </div>
              </div>
              {legal_acceptance_html}
                        <button id='login-submit' class='ayu-login-submit' type='submit'>{submit_label}</button>
                      </form>
                      {native_unlock_html}
                      <div id='login-error' class='ayu-login-error hidden' role='alert'></div>
                      {reset_panel_html}
                    </section>
                  </div>
                  <div id='reset-modal' class='ayu-reset-modal hidden' role='dialog' aria-modal='true' aria-labelledby='reset-title'>
                    <div class='ayu-reset-modal-panel'>
                      <div class='ayu-reset-modal-head'>
                        <div class='ayu-reset-modal-icon' aria-hidden='true'>
                          <svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' d='M12 9v3.75m0 3.75h.008M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z'/></svg>
                        </div>
                        <div>
                          <h2 id='reset-title'>Reset AutoYou on this computer?</h2>
                          <p>This removes local setup and then shuts the server down. Launch AutoYou again to set it up from the beginning.</p>
                        </div>
                      </div>
                      <ul>
                        <li>Saved server password and system credential entries are cleared.</li>
                        <li>Local sessions, pairing records, agent registries, and runtime databases are removed.</li>
                        <li>Installed application files and source code are left alone.</li>
                      </ul>
                      <label for='reset-confirmation'>Type RESET to continue</label>
                      <input id='reset-confirmation' class='ayu-login-input' type='text' autocomplete='off' spellcheck='false'>
                      <div id='reset-status' class='ayu-login-error hidden' role='alert'></div>
                      <div class='ayu-reset-modal-actions'>
                        <button id='cancel-reset' class='ayu-reset-cancel' type='button'>Cancel</button>
                        <button id='confirm-reset' class='ayu-reset-confirm' type='button'>Erase and Shut Down</button>
                      </div>
                    </div>
                  </div>

                  <div id='login-loading' class='startup-overlay hidden' aria-live='polite' aria-busy='true'>
            <div class='startup-panel'>
              <div class='ayu-login-brand'>{login_brand_html}</div>
              <div>
                <div class='ayu-login-eyebrow'>Initializing</div>
                <h2 id='startup-headline' class='startup-headline'>{startup_headline}</h2>
                <p id='startup-detail' class='startup-detail'>{startup_detail}</p>
              </div>
              <div class='progress-track'><div id='startup-progress-fill' class='progress-fill'></div></div>
              <div class='startup-meta'>
                <span id='startup-step-label'>Starting</span>
                <span id='startup-elapsed'>0s</span>
              </div>
              {server._build_boot_sweep_widget_html(
                title='Pass the time while it boots',
                description='A quiet little game &mdash; tap the dots while startup finishes. Your best run is saved locally.',
                board_id='boot-sweep-board',
                score_id='boot-sweep-score',
                best_score_id='boot-sweep-best',
                eyebrow='Optional',
                current_label='This run',
                best_label='Best run',
                note='Best run is stored on this server and survives restarts.',
              )}
            </div>
          </div>
        </div>
        {server._build_boot_sweep_runtime_script({
          'instanceName': 'loginBootSweepGame',
          'boardId': 'boot-sweep-board',
          'scoreId': 'boot-sweep-score',
          'bestScoreId': 'boot-sweep-best',
          'emptyBestText': '0',
        })}

        <script>
        (() => {{
          const form = document.getElementById('login-form');
          const submitButton = document.getElementById('login-submit');
          const errorBox = document.getElementById('login-error');
          const overlay = document.getElementById('login-loading');
          const headlineEl = document.getElementById('startup-headline');
          const detailEl = document.getElementById('startup-detail');
          const progressFill = document.getElementById('startup-progress-fill');
          const stepLabel = document.getElementById('startup-step-label');
          const elapsedEl = document.getElementById('startup-elapsed');
          const bootSweepGame = window.loginBootSweepGame || null;
          let statusTimer = null;
          let elapsedTimer = null;
          let startupClockStartedAt = 0;
          let startupClockBaseElapsed = 0;
          const submitLabel = {server.json.dumps(submit_label)};
          const startupInitialHeadline = {server.json.dumps(startup_headline)};
          const startupInitialDetail = {server.json.dumps(startup_detail)};
          const loginFailureMessage = {server.json.dumps(login_failure_message)};
          const setupPasswordMode = {server.json.dumps(setup_password_mode)};
          const passwordAlphabet = {server.json.dumps(server.SERVER_PASSWORD_GENERATOR_ALPHABET)};
          const passwordLength = {server.json.dumps(server.SERVER_PASSWORD_GENERATOR_LENGTH)};
                  const passwordInput = document.getElementById('server-password');
                  const togglePasswordButton = document.getElementById('toggle-password');
                  const generatePasswordButton = document.getElementById('generate-password');
                  const copyPasswordButton = document.getElementById('copy-password');
                  const nativeUnlockButton = document.getElementById('native-unlock');
                  const openResetButton = document.getElementById('open-reset-modal');
                  const resetModal = document.getElementById('reset-modal');
                  const cancelResetButton = document.getElementById('cancel-reset');
                  const confirmResetButton = document.getElementById('confirm-reset');
                  const resetConfirmationInput = document.getElementById('reset-confirmation');
                  const resetStatusBox = document.getElementById('reset-status');
                  if (nativeUnlockButton) {{
                    const nativeLabel = nativeUnlockButton.querySelector('span');
                    nativeUnlockButton.dataset.originalLabel = nativeLabel ? nativeLabel.textContent : 'Unlock with system credentials';
                  }}

          function generatePassword(length) {{
            const size = Math.max(16, Math.min(128, Number(length) || 24));
            const alphabet = passwordAlphabet || 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*-_=+?';
            const values = new Uint32Array(size);
            if (window.crypto && window.crypto.getRandomValues) {{
              window.crypto.getRandomValues(values);
            }} else {{
              for (let i = 0; i < size; i += 1) {{
                values[i] = Math.floor(Math.random() * 4294967296);
              }}
            }}
            let password = '';
            for (let i = 0; i < size; i += 1) {{
              password += alphabet[values[i] % alphabet.length];
            }}
            return password;
          }}

          function setPasswordValue(value) {{
            if (!passwordInput) return;
            passwordInput.value = value || '';
            passwordInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
            passwordInput.focus();
          }}

          function setPasswordVisible(visible) {{
            if (!passwordInput || !togglePasswordButton) return;
            passwordInput.type = visible ? 'text' : 'password';
            togglePasswordButton.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
            togglePasswordButton.setAttribute('title', visible ? 'Hide password' : 'Show password');
          }}

          if (togglePasswordButton) {{
            togglePasswordButton.addEventListener('click', () => {{
              setPasswordVisible(passwordInput ? passwordInput.type === 'password' : false);
            }});
          }}
          if (generatePasswordButton && setupPasswordMode) {{
            generatePasswordButton.addEventListener('click', () => {{
              setPasswordValue(generatePassword(passwordLength));
              setPasswordVisible(true);
            }});
          }}
                  if (copyPasswordButton && setupPasswordMode) {{
                    copyPasswordButton.addEventListener('click', async () => {{
                      const value = passwordInput ? passwordInput.value : '';
              if (!value || !navigator.clipboard) return;
              try {{
                await navigator.clipboard.writeText(value);
                const copiedLabel = copyPasswordButton.querySelector('span');
                if (copiedLabel) copiedLabel.textContent = 'Copied';
                window.setTimeout(() => {{
                  const label = copyPasswordButton.querySelector('span');
                  if (label) label.textContent = 'Copy';
                }}, 1400);
              }} catch (error) {{
                // Clipboard permissions vary by browser context; selection still lets the user copy manually.
                passwordInput.select();
                      }}
                    }});
                  }}

                  if (nativeUnlockButton) {{
                    nativeUnlockButton.addEventListener('click', async () => {{
                      clearError();
                      setNativeUnlockSubmitting(true);
                      setSubmitting(true);
                      if (!isAlreadyInitialized) {{
                        showOverlay();
                      }}
                      try {{
                        const response = await fetch('/api/login/native-unlock', {{
                          method: 'POST',
                          headers: {{
                            'X-AutoYou-Async': '1',
                            'Accept': 'application/json',
                          }},
                          credentials: 'same-origin',
                        }});
                        const payload = await response.json().catch(() => ({{}}));
                        if (!response.ok || !payload.success) {{
                          if (!isAlreadyInitialized) {{
                            hideOverlay();
                          }}
                          setSubmitting(false);
                          setNativeUnlockSubmitting(false);
                          showError(payload.error_message || 'System credential unlock did not complete.');
                          return;
                        }}
                        if (!isAlreadyInitialized) {{
                          renderStatus({{
                            status: 'complete',
                            headline: 'Initialization complete',
                            detail: payload.detail || 'Opening dashboard...',
                            step: payload.total_steps || 8,
                            total_steps: payload.total_steps || 8,
                            elapsed_seconds: payload.elapsed_seconds || 0,
                          }});
                        }}
                        window.location.assign(payload.redirect || '/');
                      }} catch (error) {{
                        if (!isAlreadyInitialized) {{
                          hideOverlay();
                        }}
                        setSubmitting(false);
                        setNativeUnlockSubmitting(false);
                        showError('System credential unlock could not reach the server. Please try the password field.');
                      }}
                    }});
                  }}

                  if (openResetButton) {{
                    openResetButton.addEventListener('click', openResetModal);
                  }}
                  if (cancelResetButton) {{
                    cancelResetButton.addEventListener('click', closeResetModal);
                  }}
                  if (resetModal) {{
                    resetModal.addEventListener('click', (event) => {{
                      if (event.target === resetModal) closeResetModal();
                    }});
                  }}
                  if (confirmResetButton) {{
                    confirmResetButton.addEventListener('click', async () => {{
                      const confirmation = resetConfirmationInput ? resetConfirmationInput.value.trim().toUpperCase() : '';
                      if (confirmation !== 'RESET') {{
                        showResetStatus('Type RESET exactly to confirm.', true);
                        return;
                      }}
                      confirmResetButton.disabled = true;
                      confirmResetButton.textContent = 'Resetting...';
                      showResetStatus('Clearing local AutoYou data and shutting down...', false);
                      try {{
                        const response = await fetch('/api/login/factory-reset', {{
                          method: 'POST',
                          headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                          }},
                          credentials: 'same-origin',
                          body: JSON.stringify({{ confirmation: 'RESET' }}),
                        }});
                        const payload = await response.json().catch(() => ({{}}));
                        if (!response.ok || !payload.success) {{
                          confirmResetButton.disabled = false;
                          confirmResetButton.textContent = 'Erase and Shut Down';
                          showResetStatus(payload.error_message || 'Reset could not start.', true);
                          return;
                        }}
                        showResetStatus('Reset started. AutoYou will close now; reopen it to start fresh.', false);
                      }} catch (error) {{
                        confirmResetButton.disabled = false;
                        confirmResetButton.textContent = 'Erase and Shut Down';
                        showResetStatus('Reset request failed before the server could respond.', true);
                      }}
                    }});
                  }}

                  function stopStatusPolling() {{
            if (statusTimer) {{
              window.clearInterval(statusTimer);
              statusTimer = null;
            }}
          }}

          function stopLocalElapsedClock() {{
            if (elapsedTimer) {{
              window.clearInterval(elapsedTimer);
              elapsedTimer = null;
            }}
          }}

                  function setSubmitting(active) {{
                    if (!submitButton) return;
                    submitButton.disabled = active;
                    submitButton.textContent = active ? 'Initializing...' : submitLabel;
                  }}

                  function setNativeUnlockSubmitting(active) {{
                    if (!nativeUnlockButton) return;
                    nativeUnlockButton.disabled = active;
                    const label = nativeUnlockButton.querySelector('span');
                    if (label) label.textContent = active ? 'Waiting for system approval...' : nativeUnlockButton.dataset.originalLabel;
                  }}

          function showError(message) {{
            if (!errorBox) return;
            errorBox.textContent = message;
            errorBox.classList.remove('hidden');
          }}

                  function clearError() {{
                    if (!errorBox) return;
                    errorBox.textContent = '';
                    errorBox.classList.add('hidden');
                  }}

                  function showResetStatus(message, isError) {{
                    if (!resetStatusBox) return;
                    resetStatusBox.textContent = message || '';
                    resetStatusBox.classList.toggle('hidden', !message);
                    resetStatusBox.style.color = isError ? '' : 'var(--ayu-text)';
                    resetStatusBox.style.background = isError ? '' : 'rgba(20,184,166,.10)';
                    resetStatusBox.style.borderColor = isError ? '' : 'rgba(20,184,166,.22)';
                  }}

                  function openResetModal() {{
                    if (!resetModal) return;
                    showResetStatus('', true);
                    if (resetConfirmationInput) resetConfirmationInput.value = '';
                    resetModal.classList.remove('hidden');
                    window.setTimeout(() => {{
                      if (resetConfirmationInput) resetConfirmationInput.focus();
                    }}, 50);
                  }}

                  function closeResetModal() {{
                    if (!resetModal) return;
                    resetModal.classList.add('hidden');
                  }}

          function fmtElapsed(sec) {{
            const s = Math.max(0, Math.round(Number(sec) || 0));
            if (s < 60) return s + 's';
            const m = Math.floor(s / 60);
            const r = s % 60;
            if (m < 60) return r > 0 ? `${{m}}m ${{r}}s` : `${{m}}m`;
            const h = Math.floor(m / 60);
            const rm = m % 60;
            return rm > 0 ? `${{h}}h ${{rm}}m` : `${{h}}h`;
          }}

          function fmtUptime(sec) {{
            const s = Math.max(0, Math.round(Number(sec) || 0));
            const days = Math.floor(s / 86400);
            const hours = Math.floor((s % 86400) / 3600);
            const mins = Math.floor((s % 3600) / 60);
            return `Running for ${{days}} days ${{hours}} hours ${{mins}} mins`;
          }}

          function updateLocalElapsedClock() {{
            if (!elapsedEl) return;
            const localDelta = startupClockStartedAt > 0 ? ((Date.now() - startupClockStartedAt) / 1000) : 0;
            elapsedEl.textContent = fmtElapsed(startupClockBaseElapsed + localDelta);
          }}

          function syncLocalElapsedClock(sec) {{
            startupClockBaseElapsed = Math.max(0, Number(sec) || 0);
            startupClockStartedAt = Date.now();
            updateLocalElapsedClock();
            if (!elapsedTimer) {{
              elapsedTimer = window.setInterval(updateLocalElapsedClock, 1000);
            }}
          }}

          function renderStatus(data) {{
            if (!data) return;
            const total = Math.max(1, Number(data.total_steps) || 8);
            const rawStep = Math.max(0, Number(data.step) || 0);
            const isTerminal = data.status === 'complete' || data.status === 'error';
            const normalizedStep = data.status === 'complete' ? total : Math.max(1, rawStep || 1);
            const progress = data.status === 'complete'
              ? 100
              : Math.max(10, Math.min(96, Math.round((normalizedStep / total) * 100)));
            if (headlineEl) headlineEl.textContent = data.headline || 'Initializing AutoYou';
            if (detailEl) detailEl.textContent = data.detail || 'Preparing services.';
            if (progressFill) progressFill.style.width = progress + '%';
            if (stepLabel) {{
              if (data.status === 'complete') {{
                if (data.uptime_seconds !== undefined) {{
                  stepLabel.textContent = fmtUptime(data.uptime_seconds);
                }} else {{
                  stepLabel.textContent = 'Ready';
                }}
              }} else {{
                stepLabel.textContent = `Step ${{normalizedStep}} of ${{total}}`;
              }}
            }}
            syncLocalElapsedClock(data.elapsed_seconds);
            if (isTerminal) {{
              stopStatusPolling();
              stopLocalElapsedClock();
              if (elapsedEl) elapsedEl.textContent = fmtElapsed(data.elapsed_seconds);
              if (bootSweepGame) {{
                void bootSweepGame.stop({{ report: false }});
              }}
            }}
          }}

          async function pollStatus() {{
            try {{
              const response = await fetch('/api/login-startup-status', {{
                method: 'GET',
                cache: 'no-store',
                credentials: 'same-origin',
              }});
              if (!response.ok) return;
              const data = await response.json();
              renderStatus(data);
            }} catch (error) {{
              // Keep the current UI state; the main login request will still decide success/failure.
            }}
          }}

          function showOverlay() {{
            if (!overlay) return;
            overlay.classList.remove('hidden');
            renderStatus({{
              status: 'starting',
              headline: startupInitialHeadline,
              detail: startupInitialDetail,
              step: 1,
              total_steps: 8,
              elapsed_seconds: 0,
            }});
            if (bootSweepGame) {{
              bootSweepGame.start();
            }}
            syncLocalElapsedClock(0);
            pollStatus();
            statusTimer = window.setInterval(pollStatus, 900);
          }}

          function hideOverlay() {{
            stopStatusPolling();
            stopLocalElapsedClock();
            if (bootSweepGame) {{
              void bootSweepGame.stop({{ report: false }});
            }}
            if (overlay) overlay.classList.add('hidden');
          }}

          let isAlreadyInitialized = false;

          async function checkInitialStatus() {{
            try {{
              const response = await fetch('/api/login-startup-status', {{
                method: 'GET',
                cache: 'no-store',
                credentials: 'same-origin',
              }});
              if (!response.ok) return;
              const data = await response.json();
              if (data && data.initialized) {{
                isAlreadyInitialized = true;
              }}
            }} catch (error) {{
              // ignore
            }}
          }}
          checkInitialStatus();

          if (!form || !window.fetch) return;

          form.addEventListener('submit', async (event) => {{
            event.preventDefault();
            clearError();
            setSubmitting(true);
            if (!isAlreadyInitialized) {{
              showOverlay();
            }}

            try {{
              const response = await fetch('/login', {{
                method: 'POST',
                body: new FormData(form),
                headers: {{
                  'X-AutoYou-Async': '1',
                  'Accept': 'application/json',
                }},
                credentials: 'same-origin',
              }});
              const payload = await response.json().catch(() => ({{}}));
              if (!response.ok || !payload.success) {{
                if (!isAlreadyInitialized) {{
                  hideOverlay();
                }}
                setSubmitting(false);
                showError(payload.error_message || loginFailureMessage);
                return;
              }}
              if (!isAlreadyInitialized) {{
                renderStatus({{
                  status: 'complete',
                  headline: 'Initialization complete',
                  detail: payload.detail || 'Opening dashboard...',
                  step: payload.total_steps || 8,
                  total_steps: payload.total_steps || 8,
                  elapsed_seconds: payload.elapsed_seconds || 0,
                }});
              }}
              window.location.assign(payload.redirect || '/');
            }} catch (error) {{
              if (!isAlreadyInitialized) {{
                hideOverlay();
              }}
              setSubmitting(false);
              showError('Connection lost while initializing the server. Please try again.');
            }}
          }});
        }})();
        </script>
        """
        return server._html_page(body)

    @admin_app.post("/login")
    async def admin_login(request: Request, password: str = Form(...), terms_accepted: Optional[str] = Form(None)):
        transport_error = server._require_loopback_or_https_request(request, allow_local_pair=True)
        if transport_error:
            return transport_error
        async_mode = server._is_async_login_request(request)
        # Rate limit login attempts (H-2: real client IP when behind bridge)
        client_ip = server._rate_limit_client_key(request)
        if not server.LOGIN_RATE_LIMITER.is_allowed(client_ip):
            server.LOGGER.warning(f"Rate limit exceeded for login from {client_ip}")
            if async_mode:
                return JSONResponse(
                    status_code=429,
                    content={
                        "success": False,
                        "error_message": "Too many attempts. Please wait a minute before trying again.",
                    },
                )
            return server._html_page("""
            <div class='card'>
              <h2>Too many attempts</h2>
              <p class='muted'>Please wait a minute before trying again.</p>
              <a href='/login'>Try again</a>
            </div>
            """)
        normalized_password = str(password or "").strip()
        first_run = not server._saved_config_exists()
        agreement_pending_unlock = bool(
            not first_run
            and server.is_license_acknowledgement_pending_unlock(anchor=server.__file__)
        )
        legal_acceptance_required = bool(
            first_run
            or (
                not agreement_pending_unlock
                and not server.is_license_acknowledged(anchor=server.__file__)
            )
        )
        waiting_detail = (
            "Enter a server password to create the initial encrypted configuration."
            if first_run else
            "Enter the server password to decrypt configuration."
        )

        def terms_required_response():
            if async_mode:
                return JSONResponse(
                    status_code=428,
                    content={
                        "success": False,
                        "error_message": "Accept the current AutoYou Terms of Use (EULA), License, responsibility terms, warranty disclaimer, and liability limits before continuing. Personal, non-commercial use is permitted by default; Enterprise Use requires a separate written OpenStorey agreement.",
                        "agreement_required": True,
                        "current_agreement_version": server.CURRENT_AGREEMENT_VERSION,
                    },
                )
            response = server._html_page("""
            <div class='card'>
              <h2>Terms acceptance required</h2>
              <p class='muted'>Accept the current AutoYou Terms of Use (EULA), License, responsibility terms, warranty disclaimer, and liability limits before continuing. Personal, non-commercial use is permitted by default; Enterprise Use requires a separate written OpenStorey agreement.</p>
              <a href='/login'>Try again</a>
            </div>
            """)
            response.status_code = 428
            return response

        if not normalized_password:
            server._update_startup_status(
                status="idle",
                headline="Waiting for password",
                detail=waiting_detail,
                step=0,
                total_steps=8,
            )
            if async_mode:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error_message": "Server password is required.",
                    },
                )
            return server._html_page("""
            <div class='card'>
              <h2>Password required</h2>
              <p class='muted'>Enter a server password to continue.</p>
              <a href='/login'>Try again</a>
            </div>
            """)

        if legal_acceptance_required and not terms_accepted:
            return terms_required_response()

        ks = server._get_server_keystore()
        keystore_available = ks is not None and ks.is_available()
        preferred_store = server.CONFIG_STORE_KEYSTORE if keystore_available else server.CONFIG_STORE_ENCRYPTED

        def locked_config_response(detail: str):
            server.LOGGER.warning("admin_login: %s", detail)
            server._update_startup_status(
                status="error",
                headline="Configuration locked",
                detail=detail,
                step=0,
                total_steps=8,
                error=detail,
            )
            if async_mode:
                return JSONResponse(
                    status_code=503,
                    content={
                        "success": False,
                        "error_message": detail,
                    },
                )
            response = server._html_page(f"""
            <div class='card'>
              <h2>Configuration locked</h2>
              <p class='muted'>{server.html.escape(detail)}</p>
              <a href='/login'>Try again</a>
            </div>
            """)
            response.status_code = 503
            return response

        if (
            not first_run
            and not keystore_available
            and (server.os.path.exists(server.CONFIG_KEYSTORE_PATH) or (ks is not None and ks.exists()))
            and not server._encrypted_config_exists()
        ):
            detail = (
                "Saved configuration is protected by the OS keystore, but the keystore "
                "backend is unavailable. The existing config was left unchanged."
            )
            return locked_config_response(detail)

        if not first_run and keystore_available and ks.exists() and not server._encrypted_config_exists() and ks.load() is None:
            detail = (
                "Saved configuration is protected by the OS keystore, but it could not "
                "be unlocked. The existing config was left unchanged."
            )
            return locked_config_response(detail)

        if first_run:
            server._update_startup_status(
                status="starting",
                headline="Creating encrypted configuration",
                detail="No saved config was found. Creating the initial encrypted configuration.",
                step=1,
                total_steps=8,
            )
            try:
                cfg = server._save_and_reload_state_config(
                    server._build_initial_server_config(),
                    server_password=normalized_password,
                    preferred_store=preferred_store,
                )
            except Exception as exc:
                server.LOGGER.warning("admin_login: failed to create initial encrypted config: %s", exc)
                server._update_startup_status(
                    status="idle",
                    headline="Waiting for password",
                    detail=waiting_detail,
                    step=0,
                    total_steps=8,
                )
                if async_mode:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "success": False,
                            "error_message": "Failed to create the initial encrypted configuration.",
                        },
                    )
                return server._html_page("""
                <div class='card'>
                  <h2>Setup failed</h2>
                  <p class='muted'>The initial encrypted configuration could not be created.</p>
                  <a href='/login'>Try again</a>
                </div>
                """)
            try:
                server.record_license_acknowledgement(anchor=server.__file__, accepted_by="admin_login_first_run")
            except Exception as exc:
                detail = "Failed to save the first-run license agreement acknowledgement."
                server.LOGGER.warning("admin_login: %s: %s", detail, exc)
                server._update_startup_status(
                    status="error",
                    headline="Setup failed",
                    detail=detail,
                    step=0,
                    total_steps=8,
                    error=detail,
                )
                if async_mode:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "success": False,
                            "error_message": detail,
                        },
                    )
                return server._html_page("""
                <div class='card'>
                  <h2>Setup failed</h2>
                  <p class='muted'>The first-run agreement acknowledgement could not be saved.</p>
                  <a href='/login'>Try again</a>
                </div>
                """)
            server.LOGGER.info("admin_login: created initial encrypted config after first-run login.")
        else:
            server._update_startup_status(
                status="starting",
                headline="Decrypting configuration",
                detail="Validating the password and unlocking the saved configuration.",
                step=1,
                total_steps=8,
            )
            active_store = server.CONFIG_STORE_NONE
            cfg, active_store = server._resolve_config_for_password(normalized_password)
            default_rotation_cfg = None
            default_rotation_store = server.CONFIG_STORE_NONE
            if cfg is None and (
                getattr(server.STATE, "used_default_password", False)
                and normalized_password != server.DEFAULT_SERVER_PASSWORD
            ):
                default_rotation_cfg, default_rotation_store = server._resolve_config_for_password(server.DEFAULT_SERVER_PASSWORD)

            if cfg is None and default_rotation_cfg is not None:
                server._update_startup_status(
                    status="starting",
                    headline="Saving new server password",
                    detail="Default bootstrap password was active. Rotating this server to the password you entered.",
                    step=1,
                    total_steps=8,
                )
                try:
                    active_store = server._persist_state_config(
                        default_rotation_cfg,
                        server_password=normalized_password,
                        preferred_store=preferred_store if default_rotation_store != server.CONFIG_STORE_KEYSTORE else server.CONFIG_STORE_KEYSTORE,
                    )
                    cfg = server.STATE.config or default_rotation_cfg
                    server.LOGGER.info("admin_login: rotated default bootstrap password during setup unlock.")
                except Exception as exc:
                    detail = "Failed to rotate the default bootstrap password."
                    server.LOGGER.warning("admin_login: %s: %s", detail, exc)
                    server._update_startup_status(
                        status="error",
                        headline="Setup failed",
                        detail=detail,
                        step=0,
                        total_steps=8,
                        error=detail,
                    )
                    if async_mode:
                        return JSONResponse(
                            status_code=500,
                            content={
                                "success": False,
                                "error_message": detail,
                            },
                        )
                    response = server._html_page(f"""
                    <div class='card'>
                      <h2>Setup failed</h2>
                      <p class='muted'>{server.html.escape(detail)}</p>
                      <a href='/login'>Try again</a>
                    </div>
                    """)
                    response.status_code = 500
                    return response

            if cfg is None:
                server._update_startup_status(
                    status="idle",
                    headline="Waiting for password",
                    detail=waiting_detail,
                    step=0,
                    total_steps=8,
                )
                if async_mode:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "success": False,
                            "error_message": "Incorrect password or corrupt config.",
                        },
                    )
                return server._html_page("""
                <div class='card'>
                  <h2>Login failed</h2>
                  <p class='muted'>Incorrect password or unreadable config store.</p>
                  <a href='/login'>Try again</a>
                </div>
                """)

            try:
                # Validate the existing Maximus key before changing config
                # storage or re-recording an acknowledgement. A denied
                # credential must leave all protected state untouched.
                server._configure_secure_storage_for_config(cfg, password=normalized_password)
            except server.SecureStorageError as exc:
                detail = (
                    "Secure Professional Maximus storage could not be initialized. "
                    "Allow access to the existing system credential and try again."
                )
                server.LOGGER.warning("admin_login: %s: %s", detail, exc)
                return locked_config_response(detail)

            if agreement_pending_unlock:
                legal_acceptance_required = not server.is_license_acknowledged(anchor=server.__file__)
                if legal_acceptance_required and not terms_accepted:
                    return terms_required_response()

            defaults_changed = (
                server._apply_default_security_config(cfg)
                or server._apply_default_tunnelmole_config(cfg)
                or server._apply_default_client_identity_config(cfg)
                or server._apply_default_agent_frontends_config(cfg)
                or server._apply_default_bluetooth_pairing_config(cfg)
                or server._apply_default_ice_servers(cfg)
                or server._apply_default_speech_config(cfg)
                or server._apply_default_onboarding_config(cfg)
                or server._apply_default_cloud_config(cfg)
                or server._apply_default_autoyou_page_config(cfg)
            )
            if active_store == server.CONFIG_STORE_KEYSTORE:
                server._set_config_session(
                    config_store=server.CONFIG_STORE_KEYSTORE,
                    server_password=normalized_password,
                )
                server.STATE.config = cfg
                if defaults_changed:
                    try:
                        server._persist_state_config(
                            cfg,
                            server_password=normalized_password,
                            preferred_store=server.CONFIG_STORE_KEYSTORE,
                        )
                    except Exception as exc:
                        server.LOGGER.warning("admin_login: failed to persist unlocked keystore config: %s", exc)
                        server._set_config_session(
                            config_store=server.CONFIG_STORE_KEYSTORE,
                            server_password=normalized_password,
                        )
                        server.STATE.config = cfg
            else:
                server._set_config_session(
                    config_store=server.CONFIG_STORE_ENCRYPTED,
                    server_password=normalized_password,
                    config_unlock_password=normalized_password,
                )
                server.STATE.config = cfg
                if defaults_changed or keystore_available:
                    try:
                        server._persist_state_config(
                            cfg,
                            server_password=normalized_password,
                            preferred_store=preferred_store,
                        )
                    except Exception as exc:
                        server.LOGGER.warning("admin_login: failed to persist unlocked config: %s", exc)
                        server._set_config_session(
                            config_store=server.CONFIG_STORE_ENCRYPTED,
                            server_password=normalized_password,
                            config_unlock_password=normalized_password,
                        )
                        server.STATE.config = cfg
            if legal_acceptance_required:
                try:
                    server.record_license_acknowledgement(anchor=server.__file__, accepted_by="admin_login_reaccept")
                except Exception as exc:
                    detail = "Failed to save the current agreement acknowledgement."
                    server.LOGGER.warning("admin_login: %s: %s", detail, exc)
                    server._update_startup_status(
                        status="error",
                        headline="Setup failed",
                        detail=detail,
                        step=0,
                        total_steps=8,
                        error=detail,
                    )
                    if async_mode:
                        return JSONResponse(
                            status_code=500,
                            content={
                                "success": False,
                                "error_message": detail,
                            },
                        )
                    response = server._html_page(f"""
                    <div class='card'>
                      <h2>Setup failed</h2>
                      <p class='muted'>{server.html.escape(detail)}</p>
                      <a href='/login'>Try again</a>
                    </div>
                    """)
                    response.status_code = 500
                    return response
        try:
            accepted_by = (
                "admin_login_first_run"
                if first_run
                else "admin_login_reaccept"
                if legal_acceptance_required
                else "admin_login_sync"
            )
            server._sync_unlock_metadata(normalized_password, accepted_by=accepted_by)
        except Exception as exc:
            detail = "Failed to save the unlock configuration."
            server.LOGGER.warning("admin_login: %s: %s", detail, exc)
            server._update_startup_status(
                status="error",
                headline="Setup failed",
                detail=detail,
                step=0,
                total_steps=8,
                error=detail,
            )
            if async_mode:
                return JSONResponse(status_code=500, content={"success": False, "error_message": detail})
            response = server._html_page(f"""
            <div class='card'>
              <h2>Setup failed</h2>
              <p class='muted'>{server.html.escape(detail)}</p>
              <a href='/login'>Try again</a>
            </div>
            """)
            response.status_code = 500
            return response

        server.STATE.used_default_password = (normalized_password == server.DEFAULT_SERVER_PASSWORD)
        server.STATE._unlock_state_mem = "Ready"

        # Initialize all services when user logs in for the first time
        await server._initialize_services_on_startup()

        sid = str(server.uuid.uuid4())
        server.ADMIN_SESSIONS[sid] = True
        status_payload = server._startup_status_payload()
        if async_mode:
            resp = JSONResponse(
                content={
                    "success": True,
                    "redirect": "/",
                    "detail": status_payload.get("detail") or "Initialization complete.",
                    "elapsed_seconds": status_payload.get("elapsed_seconds") or 0,
                    "total_steps": status_payload.get("total_steps") or 8,
                    "startup_status": status_payload,
                }
            )
        else:
            resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie("admin_session", sid, httponly=True, samesite="Strict")
        return resp

    @admin_app.post("/api/login/native-unlock")
    async def admin_login_native_unlock(request: Request):
        loopback_error = server._require_loopback_request(request)
        if loopback_error:
            return loopback_error
        if server._is_logged_in(request):
            return JSONResponse({"success": True, "redirect": "/"})
        agreement_pending_unlock = server.is_license_acknowledgement_pending_unlock(anchor=server.__file__)
        if (not server._saved_config_exists()) or (
            not agreement_pending_unlock
            and not server.is_license_acknowledged(anchor=server.__file__)
        ):
            return JSONResponse(
                status_code=428,
                content={
                    "success": False,
                    "error_message": "Accept the current AutoYou terms with the server password before using system credential unlock.",
                    "agreement_required": True,
                    "current_agreement_version": server.CURRENT_AGREEMENT_VERSION,
                },
            )
        if not server._native_unlock_enabled():
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error_message": "System credential unlock is disabled for this AutoYou server.",
                },
            )
        if not server._keystore_server_password_available():
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error_message": "System credential unlock is not available on this machine.",
                },
            )
        password = server._load_keystore_server_password()
        if not password:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error_message": "No saved AutoYou password was found in the system credential store.",
                },
            )
        return await server.admin_login(request, password=password)

    @admin_app.post("/api/login/factory-reset")
    async def admin_login_factory_reset(request: Request):
        loopback_error = server._require_loopback_request(request)
        if loopback_error:
            return loopback_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        confirmation = str((payload or {}).get("confirmation") or "").strip().upper()
        if confirmation != "RESET":
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error_message": "Type RESET to confirm the local AutoYou reset.",
                },
            )

        cleanup = server.perform_factory_reset_cleanup()
        server._schedule_factory_reset_shutdown()
        return JSONResponse(
            {
                "success": True,
                "message": "AutoYou local data reset started. The server is shutting down.",
                "shutdown": True,
                "cleanup": cleanup,
            }
        )

    @admin_app.get("/logout")
    async def admin_logout(request: Request):
        sid = request.cookies.get("admin_session", "")
        if sid and sid in server.ADMIN_SESSIONS:
            server.ADMIN_SESSIONS.pop(sid, None)
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie("admin_session")
        return resp

    @admin_app.get("/auto-login")
    async def auto_login(request: Request):
        return RedirectResponse(url="/login", status_code=302)

    @admin_app.get("/legacy-dashboard", response_class=HTMLResponse)
    async def admin_legacy_dashboard(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        status, name = await server._telegram_status()
        telegram_user_status, telegram_user_name = await server._telegram_user_status()
        signal_status, signal_name = await server._signal_status()
        whatsapp_status, whatsapp_name = await server._whatsapp_status()

        # Get Ollama and Google API status
        ollama_status, ollama_status_color, ollama_api_base, ollama_models_count, ollama_selected_model = await server._ollama_status()
        google_status, google_status_color, use_google_api, google_model, google_api_key_status = await server._google_api_status()
        ai_provider_summary = await server._ai_provider_summary()

        # Get AI Agent Server status
        ai_agent_status = await server._ai_agent_server_status()

        # Get Tunnelmole status
        tunnelmole_status_info = server.get_tunnelmole_status()
        tunnelmole_status = tunnelmole_status_info.get("status", "Unknown")
        tunnelmole_url = tunnelmole_status_info.get("public_url", "-") or "Not available"

        # Check for success banners
        qp = dict(request.query_params)
        banner = ""
        saved = qp.get("saved")
        if saved == "telegram":
            banner += "<div class='card success'><b>Success:</b> Telegram configuration saved.</div>"
        elif saved == "signal":
            banner += "<div class='card success'><b>Success:</b> Signal configuration saved.</div>"
        elif saved == "whatsapp":
            banner += "<div class='card success'><b>Success:</b> WhatsApp configuration saved.</div>"
        elif saved == "rtc":
            banner += "<div class='card success'><b>Success:</b> Connection helper configuration saved.</div>"
        elif saved == "server":
            banner += "<div class='card success'><b>Success:</b> Server name updated.</div>"
        elif saved == "ollama":
            banner += "<div class='card success'><b>Success:</b> Ollama settings saved.</div>"
        elif saved == "ai_agent":
            banner += "<div class='card success'><b>Success:</b> AI Agent Server settings saved.</div>"
        elif saved == "tunnelmole":
            banner += "<div class='card success'><b>Success:</b> Public reverse proxy settings saved.</div>"
        elif saved == "autoyou_page":
            banner += "<div class='card success'><b>Success:</b> AutoYou Page settings saved.</div>"
        elif saved == "speech":
            banner += "<div class='card success'><b>Success:</b> Speech settings saved and applied.</div>"
        elif saved == "security_mode":
            banner += "<div class='card success'><b>Success:</b> Security mode updated.</div>"
        telegram_allow_code = str(qp.get("telegram_allow_code") or "").strip().upper()
        if telegram_allow_code:
            banner += (
                "<div class='card success'><b>Telegram approval code:</b> "
                f"<code class='mono'>/allow {server.html.escape(telegram_allow_code)}</code> "
                f"<span class='muted'>This one-time code expires in about {max(1, server.TELEGRAM_ALLOW_CODE_TTL_SECONDS // 60)} minutes.</span>"
                "</div>"
            )
        telegram_allow_error = str(qp.get("telegram_allow_error") or "").strip().lower()
        if telegram_allow_error == "missing_token":
            banner += "<div class='card'><b>Telegram approval failed:</b> Configure a Telegram bot token first.</div>"
        if qp.get("password") == "updated":
            banner += "<div class='card success'><b>Success:</b> Server password updated.</div>"
        if qp.get("cloud_unregistered") == "1":
            banner += "<div class='card success'><b>Success:</b> Server disconnected from AutoYou Cloud.</div>"
        # IMPORTANT: _build_wizard_status_payload calls model_library_service which
        # uses synchronous httpx with a 2.5-second timeout (_ping_ollama).  Running
        # it on the event loop blocks TTSAudioStreamTrack.recv() and causes the
        # "Audio pacing fell behind by ~2.3s" warning.  Offload to a thread.
        wizard_payload = await server.asyncio.to_thread(
            server._build_wizard_status_payload,
            bot_status=status,
            bot_name=name,
            signal_status=signal_status,
            signal_name=signal_name,
            whatsapp_status=whatsapp_status,
            whatsapp_name=whatsapp_name,
            tunnelmole_status_info=tunnelmole_status_info,
            telegram_user_status=telegram_user_status,
            telegram_user_name=telegram_user_name,
        )
        show_onboarding_wizard = server._should_show_onboarding_wizard(
            bot_status=status,
            signal_status=signal_status,
            whatsapp_status=whatsapp_status,
            runtime_status=wizard_payload["ollama"],
            query_params=qp,
        )
        cloud_config = (server.STATE.config or {}).get("cloud", {})
        cloud_status = await server._build_cloud_status_snapshot(cloud_config)
        cloud_connected = bool(cloud_status.get("connected", False))
        return server._html_page(await server._dashboard_html(status, name, signal_status, signal_name, whatsapp_status, whatsapp_name, banner,
                                              ollama_status, ollama_status_color, ollama_api_base, ollama_models_count, ollama_selected_model,
                                              google_status, google_status_color, use_google_api, google_model, google_api_key_status, ai_provider_summary, ai_agent_status,
                                              tunnelmole_status, tunnelmole_url, server.json.dumps(wizard_payload, default=str), show_onboarding_wizard,
                            cloud_config=cloud_config, cloud_connected=cloud_connected, cloud_status=cloud_status))

    @admin_app.get("/legacy")
    async def admin_legacy_dashboard_alias(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        return RedirectResponse(url="/legacy-dashboard", status_code=302)

    @admin_app.get("/guides/ollama-install", response_class=HTMLResponse)
    async def admin_guide_ollama_install(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        return server._html_page(
            server._guide_page_html(
                "Install Ollama",
                server.build_ollama_install_guide_html(),
                "Local-first setup for AutoYou's default runtime.",
            )
        )

    @admin_app.get("/guides/connectivity", response_class=HTMLResponse)
    async def admin_guide_connectivity(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        return server._html_page(
            server._guide_page_html(
                "Public Reverse Proxy & Cloud Access",
                server.build_connectivity_guide_html(),
                "Public reverse proxy, Cloud Pair, LAN, and connection-helper access patterns for AutoYou.",
            )
        )

    @admin_app.get("/guides/telegram", response_class=HTMLResponse)
    async def admin_guide_telegram(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        return server._html_page(
            server._guide_page_html(
                "Telegram Setup",
                server.build_telegram_guide_html(),
                "Pair a Telegram bot and restrict it to your own username.",
            )
        )

    @admin_app.get("/guides/speech", response_class=HTMLResponse)
    async def admin_guide_speech(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        return server._html_page(
            server._guide_page_html(
                "Speech Voices and STT Models",
                server.build_speech_guide_html(),
                "System voices, cloud TTS providers, and local faster-whisper model downloads.",
            )
        )

    @admin_app.get("/guides/bootstrap", response_class=HTMLResponse)
    async def admin_guide_bootstrap(request: Request):
        return await server._render_markdown_guide_page(
            request,
            title="Bootstrap Guide",
            subtitle="Source, staging, and first-boot setup for AutoYou across desktop environments.",
            path=server.INSTALLATION_GUIDE_PATH,
        )

    @admin_app.get("/guides/windows-build", response_class=HTMLResponse)
    async def admin_guide_windows_build(request: Request):
        return await server._render_markdown_guide_page(
            request,
            title="Windows Build Guide",
            subtitle="Packaging and validating the Windows desktop runtime and client flows.",
            path=server.WINDOWS_BUILD_GUIDE_PATH,
        )

    @admin_app.get("/guides/macos-build", response_class=HTMLResponse)
    async def admin_guide_macos_build(request: Request):
        return await server._render_markdown_guide_page(
            request,
            title="macOS Build Guide",
            subtitle="Packaging and validating AutoYou.app and related client runtimes on macOS.",
            path=server.MACOS_BUILD_GUIDE_PATH,
        )

    @admin_app.get("/guides/signal", response_class=HTMLResponse)
    async def admin_guide_signal(request: Request):
        return await server._render_markdown_guide_page(
            request,
            title="Signal Pairing",
            subtitle="Repository-native Signal QR pairing flow.",
            path=server.SIGNAL_PAIRING_GUIDE_PATH,
        )

    @admin_app.get("/guides/whatsapp", response_class=HTMLResponse)
    async def admin_guide_whatsapp(request: Request):
        return await server._render_markdown_guide_page(
            request,
            title="WhatsApp Pairing",
            subtitle="Repository-native WhatsApp QR pairing flow.",
            path=server.WHATSAPP_PAIRING_GUIDE_PATH,
        )

    @admin_app.get("/guides/doc/{doc_id}", response_class=HTMLResponse)
    async def admin_guide_doc(doc_id: str, request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        doc = server._resolve_guide_doc(doc_id)
        if not doc:
            return PlainTextResponse("Guide not found", status_code=404)
        return server._html_page(
            server._guide_page_html(doc["title"], doc["html"], doc["subtitle"])
        )

    @admin_app.get("/admin/guides/content")
    async def admin_guides_content(request: Request, id: str = ""):
        """Content-only JSON for the in-shell Help & Guides reader."""
        redir = server._require_login(request)
        if redir:
            return redir
        doc = server._resolve_guide_doc(id)
        if not doc:
            return JSONResponse({"error": "not_found", "id": id}, status_code=404)
        return JSONResponse(
            {
                "id": doc["id"],
                "title": doc["title"],
                "subtitle": doc["subtitle"],
                "category": doc["category"],
                "html": doc["html"],
            }
        )

    @admin_app.get("/api/wizard/status")
    async def admin_wizard_status(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        bot_status, bot_name = await server._telegram_status()
        telegram_user_status, telegram_user_name = await server._telegram_user_status()
        signal_status, signal_name = await server._signal_status()
        whatsapp_status, whatsapp_name = await server._whatsapp_status()
        tunnelmole_status_info = server.get_tunnelmole_status()
        # Off-load to thread: _build_wizard_status_payload calls model_library_service
        # which uses a synchronous httpx.Client(timeout=2.5) to ping Ollama.  Without
        # this, the 7-second JavaScript poll causes ~2.3s event-loop blocks that chop
        # WebRTC audio.
        payload = await server.asyncio.to_thread(
            server._build_wizard_status_payload,
            bot_status=bot_status,
            bot_name=bot_name,
            signal_status=signal_status,
            signal_name=signal_name,
            whatsapp_status=whatsapp_status,
            whatsapp_name=whatsapp_name,
            tunnelmole_status_info=tunnelmole_status_info,
            telegram_user_status=telegram_user_status,
            telegram_user_name=telegram_user_name,
        )
        # from __debug_provenance_y__ import legal
        # Compute ai_provider_summary separately (it's async) and merge into payload
        # so the wizard JS pill can show the active provider name/model.
        try:
            payload["ai_provider_summary"] = await server._ai_provider_summary()
        except Exception:
            payload["ai_provider_summary"] = "Unknown"

        payload["success"] = True
        payload["show_wizard"] = server._should_show_onboarding_wizard(
            bot_status=bot_status,
            signal_status=signal_status,
            whatsapp_status=whatsapp_status,
            runtime_status=payload["ollama"],
            query_params=dict(request.query_params),
        )
        payload["skip_advanced_url"] = "/?advanced=1"
        return JSONResponse(payload)

    @admin_app.post("/api/wizard/complete")
    async def admin_complete_wizard(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        block_reason = server._config_write_block_reason()
        if block_reason:
            return server._json_config_write_blocked_response(block_reason)

        try:
            body = await request.json()
        except Exception:
            body = {}
        completed = bool(body.get("completed", True))

        cfg = server._loaded_config_for_update()
        onboarding = server._onboarding_config(cfg)
        onboarding["wizard_completed"] = completed
        onboarding["wizard_completed_at"] = server.time.strftime("%Y-%m-%dT%H:%M:%SZ", server.time.gmtime()) if completed else ""
        server._persist_state_config(cfg)
        return JSONResponse({"success": True, "wizard_completed": completed})

    @admin_app.get("/api/software-update/status")
    async def software_update_status(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(await server._software_update_status_payload())

    @admin_app.post("/api/software-update/apply")
    async def software_update_apply(request: Request):
        auth_error = server._require_api_login(request)
        if auth_error:
            return auth_error
        return server._json_response_no_store(await server._software_update_status_payload(apply=True))

    @admin_app.get("/assets/admin-ui.css")
    async def admin_ui_css_asset():
        return server._admin_ui_asset_response(server._admin_ui_css_file(), "text/css")

    @admin_app.get("/assets/admin-ui.js")
    async def admin_ui_js_asset():
        return server._admin_ui_asset_response(server._admin_ui_js_file(), "text/javascript")

    @admin_app.get("/")
    async def admin_dashboard_shell(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        legacy_flag = str(request.query_params.get("legacy") or "").strip().lower()
        if legacy_flag not in {"", "0", "false", "no", "off"}:
            return RedirectResponse(url="/legacy-dashboard", status_code=302)
        if not server._admin_ui_assets_present():
            server.LOGGER.error(
                "Modern admin UI assets are missing: css=%s js=%s",
                server._admin_ui_css_file(),
                server._admin_ui_js_file(),
            )
            return PlainTextResponse(
                "Modern admin UI assets are missing. Reinstall or rebuild the bundled assets.",
                status_code=503,
            )
        return server._set_no_store_headers(HTMLResponse(server._build_admin_ui_shell_html()))

    @admin_app.get("/admin")
    async def admin_dashboard_shell_alias(request: Request):
        redir = server._require_login(request)
        if redir:
            return redir
        return RedirectResponse(url="/", status_code=302)

    return {
        "download_local_https_ca_certificate": download_local_https_ca_certificate,
        "admin_logo_asset": admin_logo_asset,
        "admin_logo_ico_asset": admin_logo_ico_asset,
        "admin_favicon_asset": admin_favicon_asset,
        "admin_apple_touch_icon_asset": admin_apple_touch_icon_asset,
        "admin_profile_image_asset": admin_profile_image_asset,
        "admin_ui_get_profile_image": admin_ui_get_profile_image,
        "admin_ui_upload_profile_image": admin_ui_upload_profile_image,
        "admin_ui_delete_profile_image": admin_ui_delete_profile_image,
        "admin_ui_desktop_assets": admin_ui_desktop_assets,
        "admin_ui_desktop_asset_setup_prompt": admin_ui_desktop_asset_setup_prompt,
        "admin_ui_save_desktop_asset_preferences": admin_ui_save_desktop_asset_preferences,
        "admin_ui_import_desktop_asset_pack": admin_ui_import_desktop_asset_pack,
        "admin_ui_remove_desktop_asset_pack": admin_ui_remove_desktop_asset_pack,
        "page_agent_get_avatar": page_agent_get_avatar,
        "page_agent_save_avatar": page_agent_save_avatar,
        "page_agent_delete_avatar": page_agent_delete_avatar,
        "get_license_file": get_license_file,
        "get_notice_file": get_notice_file,
        "get_third_party_notices_file": get_third_party_notices_file,
        "get_sbom_file": get_sbom_file,
        "admin_login_page": admin_login_page,
        "admin_login": admin_login,
        "admin_login_native_unlock": admin_login_native_unlock,
        "admin_login_factory_reset": admin_login_factory_reset,
        "admin_logout": admin_logout,
        "auto_login": auto_login,
        "admin_legacy_dashboard": admin_legacy_dashboard,
        "admin_legacy_dashboard_alias": admin_legacy_dashboard_alias,
        "admin_guide_ollama_install": admin_guide_ollama_install,
        "admin_guide_connectivity": admin_guide_connectivity,
        "admin_guide_telegram": admin_guide_telegram,
        "admin_guide_speech": admin_guide_speech,
        "admin_guide_bootstrap": admin_guide_bootstrap,
        "admin_guide_windows_build": admin_guide_windows_build,
        "admin_guide_macos_build": admin_guide_macos_build,
        "admin_guide_signal": admin_guide_signal,
        "admin_guide_whatsapp": admin_guide_whatsapp,
        "admin_guide_doc": admin_guide_doc,
        "admin_guides_content": admin_guides_content,
        "admin_wizard_status": admin_wizard_status,
        "admin_complete_wizard": admin_complete_wizard,
        "software_update_status": software_update_status,
        "software_update_apply": software_update_apply,
        "admin_ui_css_asset": admin_ui_css_asset,
        "admin_ui_js_asset": admin_ui_js_asset,
        "admin_dashboard_shell": admin_dashboard_shell,
        "admin_dashboard_shell_alias": admin_dashboard_shell_alias
    }
