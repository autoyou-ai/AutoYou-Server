# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-f4a92447ce16c964e465d950

"""Models HTTP routes for the full AutoYou server."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from typing import Any, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.ollama_capabilities import resolve_installed_ollama_model

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-f4a92447ce16c964e465d950"


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:
    @admin_app.get("/api/model-library/local")
    async def admin_model_library_local(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        cfg = server.STATE.config or server._default_config()
        ollama_cfg = cfg.get("ollama", {})
        api_base = str(ollama_cfg.get("api_base") or "http://localhost:11434")
        selected_model = str(ollama_cfg.get("model") or "")
        local_models = await server.asyncio.to_thread(server.model_library_service.list_local_models, api_base)
        # from __debug_provenance_g__ import annual
        runtime_status = await server.asyncio.to_thread(server.model_library_service.get_ollama_runtime_status, api_base, selected_model)
        return JSONResponse(
            {
                "success": True,
                "selected_model": selected_model,
                "runtime": runtime_status,
                "models": local_models,
            }
        )

    @admin_app.get("/api/model-library/catalog")
    async def admin_model_library_catalog(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        params = request.query_params
        source = str(params.get("source") or "ollama").strip().lower()
        query = str(params.get("q") or "").strip()
        page = max(1, int(str(params.get("page") or "1")))
        include_cloud = str(params.get("include_cloud") or "").strip().lower() in {"1", "true", "yes", "on"}

        try:
            if source == "ollama":
                payload = await server.asyncio.to_thread(
                    server.model_library_service.search_ollama_catalog,
                    query,
                    page=page,
                    include_cloud=include_cloud,
                )
            elif source == "huggingface":
                payload = await server.asyncio.to_thread(
                    server.model_library_service.search_huggingface_gguf,
                    query,
                    page=page,
                )
            else:
                return JSONResponse({"success": False, "error": "Unknown model source"}, status_code=400)
            payload["success"] = True
            return JSONResponse(payload)
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @admin_app.get("/api/model-library/details")
    async def admin_model_library_details(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        params = request.query_params
        source = str(params.get("source") or "ollama").strip().lower()
        identifier = str(params.get("id") or params.get("slug") or params.get("repo_id") or "").strip()
        if not identifier:
            return JSONResponse({"success": False, "error": "Missing model identifier"}, status_code=400)

        cfg = server.STATE.config or server._default_config()
        local_models = await server.asyncio.to_thread(
            server.model_library_service.list_local_models,
            str(cfg.get("ollama", {}).get("api_base") or "http://localhost:11434"),
        )
        installed_names = {str(model.get("name") or "") for model in local_models}

        try:
            if source == "ollama":
                payload = await server.asyncio.to_thread(server.model_library_service.get_ollama_model_details, identifier)
                for variant in payload.get("variants", []):
                    variant["installed"] = variant.get("name") in installed_names
            elif source == "huggingface":
                payload = await server.asyncio.to_thread(server.model_library_service.get_huggingface_repo_details, identifier)
                for file in payload.get("gguf_files", []):
                    reference = server.build_hf_ollama_reference(identifier, file.get("quantization"))
                    file["ollama_reference"] = reference
                    file["installed"] = reference in installed_names
            else:
                return JSONResponse({"success": False, "error": "Unknown model source"}, status_code=400)
            payload["success"] = True
            return JSONResponse(payload)
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @admin_app.post("/api/model-library/download")
    async def admin_model_library_download(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}

        source = str(body.get("source") or "ollama").strip().lower()
        cfg = server.STATE.config or server._default_config()
        api_base = str(cfg.get("ollama", {}).get("api_base") or "http://localhost:11434")
        title = str(body.get("title") or "").strip()

        try:
            if source == "huggingface":
                repo_id = str(body.get("repo_id") or body.get("reference") or "").strip()
                quantization = str(body.get("quantization") or "").strip() or None
                reference = server.build_hf_ollama_reference(repo_id, quantization)
                if not title:
                    title = reference
            else:
                reference = str(body.get("reference") or body.get("model") or "").strip()
                if not title:
                    title = reference

            job = await server.asyncio.to_thread(
                server.model_library_service.start_pull_job,
                source=source,
                reference=reference,
                title=title,
                api_base=api_base,
            )
            return JSONResponse({"success": True, "job": job})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    @admin_app.get("/api/model-library/downloads")
    async def admin_model_library_downloads(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        jobs = await server.asyncio.to_thread(server.model_library_service.list_jobs)
        return JSONResponse({"success": True, "jobs": jobs})

    @admin_app.get("/api/model-library/downloads/{job_id}")
    async def admin_model_library_download_status(job_id: str, request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        job = await server.asyncio.to_thread(server.model_library_service.get_job, job_id)
        if job is None:
            return JSONResponse({"success": False, "error": "Download not found"}, status_code=404)
        return JSONResponse({"success": True, "job": job})

    @admin_app.post("/api/model-library/delete")
    async def admin_model_library_delete(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}

        reference = str(body.get("model") or body.get("reference") or "").strip()
        if not reference:
            return JSONResponse({"success": False, "error": "Model name is required"}, status_code=400)

        cfg = server.STATE.config or server._default_config()
        api_base = str(cfg.get("ollama", {}).get("api_base") or "http://localhost:11434")
        selected_model = str(cfg.get("ollama", {}).get("model") or "").strip()

        result = await server.asyncio.to_thread(server.model_library_service.delete_local_model, reference, api_base)
        if result.get("status") != "success":
            return JSONResponse({"success": False, "error": result.get("message")}, status_code=400)

        payload = {
            "success": True,
            "model": reference,
            "message": result.get("message"),
            "was_selected": reference == selected_model,
        }
        if reference == selected_model:
            payload["warning"] = (
                "The deleted model was active. Select another model before restarting AutoYou AI."
            )
        return JSONResponse(payload)

    @admin_app.get("/api/speech-models/status")
    async def admin_speech_model_status(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        cfg = server.STATE.config or server._default_config()
        current_model = str(server._speech_config(cfg).get("stt", {}).get("model") or "")
        payload = await server.asyncio.to_thread(server.speech_model_library_service.get_status, current_model)
        payload["system_voices"] = server.list_system_tts_voices() if callable(server.list_system_tts_voices) else []
        payload["custom_voice"] = server.custom_voice_status()
        payload["custom_voices"] = server.list_custom_voice_statuses()
        from shared.emotivoice_tts import status as emotivoice_status
        payload["emotivoice"] = await server.asyncio.to_thread(emotivoice_status)
        payload["openai_tts_models"] = list(server.OPENAI_TTS_MODELS)
        payload["openai_tts_voices"] = list(server.OPENAI_TTS_VOICES)
        payload["stt_model_suggestions"] = list(server.STT_MODEL_SUGGESTIONS)
        payload["stt_device_suggestions"] = list(server.STT_DEVICE_SUGGESTIONS)
        payload["stt_compute_type_suggestions"] = list(server.STT_COMPUTE_TYPE_SUGGESTIONS)
        payload["success"] = True
        return JSONResponse(payload)

    @admin_app.post("/api/speech-models/download")
    async def admin_speech_model_download(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}

        model_name = str(body.get("model") or body.get("reference") or "").strip()
        if not model_name:
            return JSONResponse({"success": False, "error": "Missing STT model name"}, status_code=400)

        try:
            if model_name.lower() == "emotivoice":
                job = await server.asyncio.to_thread(server.speech_model_library_service.start_emotivoice_download_job)
            else:
                job = await server.asyncio.to_thread(server.speech_model_library_service.start_download_job, model_name)
            return JSONResponse({"success": True, "job": job})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    @admin_app.get("/api/speech-models/downloads")
    async def admin_speech_model_downloads(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)
        jobs = await server.asyncio.to_thread(server.speech_model_library_service.list_jobs)
        return JSONResponse({"success": True, "jobs": jobs})

    @admin_app.post("/api/speech-models/delete")
    async def admin_speech_model_delete(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}

        model_name = str(body.get("model") or body.get("reference") or "").strip()
        if not model_name:
            return JSONResponse({"success": False, "error": "Missing STT model name"}, status_code=400)

        cfg = server.STATE.config or server._default_config()
        selected_model = str(server._speech_config(cfg).get("stt", {}).get("model") or "").strip()

        result = await server.asyncio.to_thread(server.speech_model_library_service.delete_model, model_name)
        if result.get("status") != "success":
            return JSONResponse({"success": False, "error": result.get("message")}, status_code=400)

        payload = {
            "success": True,
            "model": model_name,
            "message": result.get("message"),
            "was_selected": model_name == selected_model,
        }
        if model_name == selected_model:
            payload["warning"] = (
                "The deleted STT model is still selected. Pick another speech model; it will be re-downloaded on next use otherwise."
            )
        return JSONResponse(payload)

    @admin_app.post("/api/model-library/select")
    async def admin_model_library_select(request: Request):
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

        requested_model_name = str(body.get("model") or body.get("reference") or "").strip()
        model_name = requested_model_name
        if not requested_model_name:
            return JSONResponse({"success": False, "error": "Model name is required"}, status_code=400)

        cfg_before = server.STATE.config or server._default_config()
        api_base = str(cfg_before.get("ollama", {}).get("api_base") or "http://localhost:11434")
        local_models = await server.asyncio.to_thread(
            server.model_library_service.list_local_models,
            api_base,
        )
        try:
            model_name = resolve_installed_ollama_model(requested_model_name, local_models)
        except ValueError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

        cfg = server._loaded_config_for_update()
        cfg.setdefault("ollama", {})["model"] = model_name
        cfg["ollama"]["model_explicit"] = True
        cfg["ollama"]["use_google_api"] = False
        active_provider = str(cfg.setdefault("ai_provider", {}).get("provider") or "ollama").strip().lower()
        cfg["ai_provider"]["provider"] = "ollama_gateway" if active_provider == "ollama_gateway" else "ollama"
        server._persist_state_config(cfg)
        server.os.environ["OLLAMA_MODEL"] = model_name
        server.os.environ["AI_PROVIDER"] = cfg["ai_provider"]["provider"]
        server.os.environ["USE_GOOGLE_API"] = "false"
        try:
            server.ollama_service.reload_from_env()
        except Exception as exc:
            server.LOGGER.warning("Failed to reload Ollama service after model selection: %s", exc)
        if cfg["ai_provider"]["provider"] == "ollama_gateway":
            try:
                from shared.ollama_gateway import reset_ollama_model_cache

                reset_ollama_model_cache()
                await server.asyncio.to_thread(server._ensure_local_ollama_runtime_ready)
            except Exception as exc:
                server.LOGGER.warning("Failed to refresh native Ollama gateway after model selection: %s", exc)

        restarted = False
        if (
            cfg["ai_provider"]["provider"] != "ollama_gateway"
            and bool(body.get("restart_ai", True))
            and cfg.get("ai_agent", {}).get("enabled", True)
        ):
            try:
                await server.restart_ai_agent_server()
                restarted = True
            except Exception as exc:
                server.LOGGER.warning("Failed to restart AI Agent Server after model selection: %s", exc)
                return JSONResponse(
                    {
                        "success": False,
                        "model": model_name,
                        "selected_model": model_name,
                        "provider": cfg["ai_provider"]["provider"],
                        "restarted_ai": False,
                        "error": f"Model was saved, but the AI Agent Server did not restart: {exc}",
                    },
                    status_code=503,
                )

        return JSONResponse(
            {
                "success": True,
                "model": model_name,
                "selected_model": model_name,
                "provider": cfg["ai_provider"]["provider"],
                "restarted_ai": restarted,
            }
        )

    @admin_app.post("/api/rtc/parse")
    async def admin_parse_rtc_bundle(request: Request):
        redir = server._require_login(request)
        if redir:
            return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}

        raw_text = str(body.get("text") or "").strip()
        mode = str(body.get("mode") or "append").strip().lower()
        existing = body.get("existing")
        existing_cfg = existing if isinstance(existing, dict) else (server.STATE.config or server._default_config()).get("rtc", {})

        try:
            parsed = server.parse_ice_servers_input(raw_text)
            merged = server.merge_rtc_config(existing_cfg, parsed["servers"], replace=(mode == "replace"))
            merged["iceServers"] = server.dedupe_ice_servers(merged.get("iceServers", []))
            return JSONResponse(
                {
                    "success": True,
                    "config": merged,
                    "detected_source": parsed.get("detected_source", ""),
                    "warnings": parsed.get("warnings", []),
                    "added_count": len(parsed.get("servers", [])),
                    "total_count": len(merged.get("iceServers", [])),
                }
            )
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    return {
        "admin_model_library_local": admin_model_library_local,
        "admin_model_library_catalog": admin_model_library_catalog,
        "admin_model_library_details": admin_model_library_details,
        "admin_model_library_download": admin_model_library_download,
        "admin_model_library_downloads": admin_model_library_downloads,
        "admin_model_library_download_status": admin_model_library_download_status,
        "admin_model_library_delete": admin_model_library_delete,
        "admin_speech_model_status": admin_speech_model_status,
        "admin_speech_model_download": admin_speech_model_download,
        "admin_speech_model_downloads": admin_speech_model_downloads,
        "admin_speech_model_delete": admin_speech_model_delete,
        "admin_model_library_select": admin_model_library_select,
        "admin_parse_rtc_bundle": admin_parse_rtc_bundle
    }
