import asyncio, json, time, re, uuid
from pathlib import Path
import httpx

BASE = "http://192.168.1.57:8000"
SEARXNG = "http://192.168.1.57:8080"
TIMEOUT_SHORT = httpx.Timeout(connect=5, read=30, write=10, pool=5)
TIMEOUT_LONG = httpx.Timeout(connect=30, read=320, write=60, pool=30)
evidence = {}


def log(step, data):
    evidence[step] = data
    print(f"[{step}] {json.dumps(data, ensure_ascii=False)[:1000]}")


async def step1_health():
    async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
        r = await client.get(f"{BASE}/api/health")
        j = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        data = {
            "status_code": r.status_code,
            "json": j,
            "status_field": j.get("status"),
            "model_configured": j.get("model_configured"),
            "endpoint_reachable": j.get("endpoint_reachable"),
            "dependency_errors": j.get("dependency_errors"),
            "checked_at": j.get("checked_at"),
            "raw_text_preview": r.text[:500],
        }
        try:
            env_text = Path(".env").read_text(encoding="utf-8", errors="ignore")
            brot_line = [l for l in env_text.splitlines() if "BROT_BASE_URL" in l]
            data["env_brot_base_url_raw"] = brot_line
            raw_val = "https://ollama.com"
            for line in brot_line:
                if "=" in line:
                    raw_val = line.split("=", 1)[1].strip()
                    break
            from app.config import Settings

            s2 = Settings(_env_file=None, brot_base_url=raw_val, brot_api_key="dummy")
            data["normalization_simulated_input"] = raw_val
            data["normalization_simulated_output"] = s2.brot_base_url
            data["normalization_expected"] = "https://ollama.com/v1"
            data["normalization_pass"] = s2.brot_base_url == "https://ollama.com/v1"
        except Exception as e:
            data["env_read_error"] = f"{type(e).__name__}: {e}"
        log("1_health", data)
        return j


async def step2_sessions():
    async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
        payload = {
            "goal": "Learn recursion with Socratic method",
            "file_ids": [],
            "grounding_mode": "grounded",
        }
        r = await client.post(f"{BASE}/api/sessions", json=payload)
        j = r.json() if r.status_code in (200, 201) else {"raw": r.text[:500]}
        data = {
            "post_status": r.status_code,
            "post_json": j,
            "post_text_preview": r.text[:500],
        }
        sid = j.get("id") if isinstance(j, dict) else None
        log("2_create_session_raw", data)
        if not sid:
            payload2 = {"goal": "Learn recursion with Socratic method", "file_ids": []}
            r2 = await client.post(f"{BASE}/api/sessions", json=payload2)
            data["retry_status"] = r2.status_code
            data["retry_text"] = r2.text[:500]
            try:
                j2 = r2.json()
                data["retry_json"] = j2
                sid = j2.get("id")
            except:
                pass
            log("2_create_retry", data)
        if sid:
            r_get = await client.get(f"{BASE}/api/sessions/{sid}")
            j_get = (
                r_get.json()
                if r_get.headers.get("content-type", "").startswith("application/json")
                else {"raw": r_get.text[:500]}
            )
            data_get = {
                "get_status": r_get.status_code,
                "get_json_keys": list(j_get.keys())
                if isinstance(j_get, dict)
                else "not dict",
                "get_preview": str(j_get)[:800],
            }
            log("2_get_session", data_get)
            r_list = await client.get(f"{BASE}/api/sessions")
            j_list = (
                r_list.json()
                if r_list.headers.get("content-type", "").startswith("application/json")
                else []
            )
            data_list = {
                "list_status": r_list.status_code,
                "list_count": len(j_list) if isinstance(j_list, list) else "not list",
                "list_preview": str(j_list)[:800],
            }
            log("2_list_sessions", data_list)
            evidence["2_create_session_raw"]["session_id"] = sid
            evidence["2_create_session_raw"]["get_status"] = data_get["get_status"]
            evidence["2_create_session_raw"]["list_status"] = data_list["list_status"]
            evidence["2_create_session_raw"]["list_count"] = data_list["list_count"]
        else:
            data["session_id"] = None
        evidence["2_sessions_combined"] = data
        return sid


async def step3_files_watch():
    async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
        r_files = await client.get(f"{BASE}/api/files")
        j_files = (
            r_files.json()
            if r_files.headers.get("content-type", "").startswith("application/json")
            else []
        )
        data_files = {
            "status": r_files.status_code,
            "count": len(j_files) if isinstance(j_files, list) else "not list",
            "preview": str(j_files)[:800],
        }
        log("3_files", data_files)
        r_watch = await client.get(f"{BASE}/api/watch")
        j_watch = (
            r_watch.json()
            if r_watch.headers.get("content-type", "").startswith("application/json")
            else {"raw": r_watch.text[:500]}
        )
        data_watch = {
            "status": r_watch.status_code,
            "json_preview": str(j_watch)[:800],
            "raw_preview": r_watch.text[:500],
        }
        log("3_watch", data_watch)
        return j_files


async def step4_upload():
    async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
        content = b"Recursion is when a function calls itself. Base case stops it."
        files = {"files": ("recursion_test.txt", content, "text/plain")}
        r = await client.post(f"{BASE}/api/files", files=files)
        data = {"status": r.status_code, "text_preview": r.text[:800]}
        try:
            j = r.json()
            data["json"] = j if isinstance(j, list) else str(j)[:800]
            if isinstance(j, list) and j:
                data["first_file"] = j[0]
                data["first_status"] = j[0].get("status")
                data["num_chunks"] = j[0].get("num_chunks")
                data["id"] = j[0].get("id")
        except Exception as e:
            data["json_error"] = f"{type(e).__name__}: {e}"
        log("4_upload", data)
        if r.status_code not in (200, 201):
            r2 = await client.get(f"{BASE}/api/files")
            j2 = r2.json() if r2.status_code == 200 else []
            data["fallback_list_count"] = len(j2) if isinstance(j2, list) else 0
            data["fallback_preview"] = str(j2)[:800]
            log("4_upload_fallback", data)
        if data.get("first_status") == "pending":
            await asyncio.sleep(2)
            fid = data.get("id")
            if fid:
                r3 = await client.get(f"{BASE}/api/files/{fid}")
                j3 = r3.json() if r3.status_code == 200 else {}
                data["retry_status"] = (
                    j3.get("status") if isinstance(j3, dict) else "not dict"
                )
                data["retry_preview"] = str(j3)[:500]
                log("4_upload_retry", {"retry": data["retry_status"]})
        return data


async def sse_collect(url, method="POST", json_body=None, timeout=TIMEOUT_LONG):
    deltas = []
    events = []
    meta = None
    done = None
    error_event = None
    citations = []
    raw_lines = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            method, url, json=json_body, headers={"Accept": "text/event-stream"}
        ) as resp:
            status = resp.status_code
            ctype = resp.headers.get("content-type", "")
            events.append({"http_status": status, "content_type": ctype})
            if status != 200:
                body = await resp.aread()
                try:
                    j = json.loads(body)
                except:
                    j = body.decode(errors="ignore")[:1000]
                events.append({"error_body": j})
                return {
                    "http_status": status,
                    "events": events,
                    "deltas": deltas,
                    "meta": meta,
                    "done": done,
                    "error": error_event,
                    "raw": raw_lines,
                }
            async for line in resp.aiter_lines():
                if line is None:
                    continue
                raw_lines.append(line)
                if line.startswith("data:"):
                    payload_str = line[5:].strip()
                    if not payload_str:
                        continue
                    try:
                        payload = json.loads(payload_str)
                    except:
                        events.append({"bad_json": payload_str[:500]})
                        continue
                    events.append(payload)
                    t = payload.get("type")
                    if t == "meta":
                        meta = payload
                    elif t == "delta":
                        deltas.append(payload.get("delta", ""))
                    elif t == "citation":
                        citations.append(payload.get("chunk_id"))
                    elif t == "done":
                        done = payload
                    elif t == "error":
                        error_event = payload
                elif line.startswith(":"):
                    events.append({"ping": line})
    full = "".join(deltas)
    return {
        "http_status": status,
        "events": events,
        "deltas": deltas,
        "full": full,
        "meta": meta,
        "done": done,
        "error": error_event,
        "citations": citations,
        "raw_lines_count": len(raw_lines),
        "raw_preview": raw_lines[:20],
    }


async def step5_turn(sid):
    url = f"{BASE}/api/sessions/{sid}/turns"
    body = {
        "message": "Explain recursion in one short step, use LaTeX for a simple recurrence like T(n)=T(n-1)+1, then ask me a Socratic question.",
        "client_msg_id": f"m-{uuid.uuid4().hex[:8]}",
    }
    result = await sse_collect(url, method="POST", json_body=body)
    full = result.get("full", "")
    has_latex = bool(
        re.search(
            r"\$\$.*?\$\$|\\\(.*?\\\)|\\\[.*?\\\]|\$[^$]+?\$", full, flags=re.DOTALL
        )
    )
    latex_detail = []
    for pat in [r"\$\$.*?\$\$", r"\\\(.*?\\\)", r"T\(n\)", r"\\begin\{", r"\\frac"]:
        if re.search(pat, full):
            latex_detail.append(pat)
    ends_with_question = full.strip().endswith("?") if full.strip() else False
    last_q = "?" in full[-200:] if full else False
    thought_markers = ["<|channel|>thought", "channel|>", "<|channel|>", "<|think|>"]
    thought_leak = 0
    for m in thought_markers:
        if m in full:
            thought_leak += full.count(m)
    leaked_deltas = [
        d for d in result.get("deltas", []) if any(m in d for m in thought_markers)
    ]
    meta = result.get("meta")
    has_web_results_in_meta = False
    if meta:
        has_web_results_in_meta = "web_results" in meta or "web" in str(meta).lower()
    data = {
        "http_status": result.get("http_status"),
        "meta": meta,
        "meta_chunks_count": len(meta.get("chunks", []))
        if meta and isinstance(meta.get("chunks"), list)
        else None,
        "meta_insufficient": meta.get("insufficient") if meta else None,
        "deltas_count": len(result.get("deltas", [])),
        "full_preview": full[:800],
        "full_length": len(full),
        "has_latex": has_latex,
        "latex_detail": latex_detail,
        "ends_with_question": ends_with_question,
        "last_200_has_question": last_q,
        "full_ends_with": full.strip()[-120:] if full.strip() else "",
        "thought_leak_count": thought_leak,
        "leaked_deltas_count": len(leaked_deltas),
        "leaked_deltas_preview": leaked_deltas[:2],
        "thought_leak_markers_found": [m for m in thought_markers if m in full],
        "citations": result.get("citations"),
        "citations_count": len(result.get("citations", [])),
        "done": result.get("done"),
        "error": result.get("error"),
        "has_web_results_in_meta": has_web_results_in_meta,
        "events_types": [
            e.get("type")
            for e in result.get("events", [])
            if isinstance(e, dict) and "type" in e
        ][:20],
        "raw_lines_count": result.get("raw_lines_count"),
    }
    log("5_turn", data)
    return result


async def step6_outputs(sid):
    kinds = [
        ("chat", "Say hello in one sentence.", 3),
        ("todo", "Make a 3-item study checklist for recursion", 3),
        ("quiz", "Create a 2-question quiz on recursion basics", 2),
        ("latex", "Give me the LaTeX for the binomial theorem", 3),
        ("teach", "Teach me recursion step 1 with a simple example", 3),
        ("mermaid", "Draw a flowchart for recursion vs iteration", 3),
    ]
    results = {}
    async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
        for kind, prompt, count in kinds:
            body = (
                {"output_kind": kind, "prompt": prompt, "count": count}
                if kind == "quiz"
                else {"output_kind": kind, "prompt": prompt}
            )
            if kind == "quiz":
                body["count"] = 2
            r = await client.post(f"{BASE}/api/sessions/{sid}/outputs", json=body)
            data = {"http_status": r.status_code, "prompt": prompt, "count": count}
            data["raw_text_preview"] = r.text[:1200]
            try:
                j = (
                    r.json()
                    if r.headers.get("content-type", "").startswith("application/json")
                    else None
                )
                if j is not None:
                    data["json_kind"] = j.get("kind")
                    data["validation"] = j.get("validation")
                    data["validation_attempts"] = (
                        j.get("validation", {}).get("attempts")
                        if isinstance(j.get("validation"), dict)
                        else None
                    )
                    data["citations"] = j.get("citations")
                    data["citations_count"] = (
                        len(j.get("citations", []))
                        if isinstance(j.get("citations"), list)
                        else None
                    )
                    data["output_id"] = j.get("output_id")
                    content = j.get("content")
                    if isinstance(content, dict):
                        data["content_keys"] = list(content.keys())
                        data["content_preview"] = json.dumps(
                            content, ensure_ascii=False
                        )[:800]
                        if kind == "teach":
                            data["teach_actions"] = content.get("actions")
                            data["teach_actions_count"] = (
                                len(content.get("actions", []))
                                if isinstance(content.get("actions"), list)
                                else 0
                            )
                            if isinstance(content.get("actions"), list):
                                data["teach_actions_labels"] = [
                                    (a.get("id"), a.get("label"))
                                    for a in content.get("actions")
                                ]
                            data["latex_blocks_count"] = (
                                len(content.get("latex_blocks", []))
                                if isinstance(content.get("latex_blocks"), list)
                                else 0
                            )
                        if kind == "mermaid":
                            data["diagram_type"] = content.get("diagram_type")
                            data["source_preview"] = content.get("source", "")[:300]
                        if kind == "quiz":
                            qs = content.get("questions", [])
                            data["quiz_questions_count"] = (
                                len(qs) if isinstance(qs, list) else 0
                            )
                            if qs and isinstance(qs, list) and qs:
                                data["quiz_first_question_preview"] = qs[0].get(
                                    "question", ""
                                )[:200]
                        if kind == "todo":
                            items = content.get("items", [])
                            data["todo_items_count"] = (
                                len(items) if isinstance(items, list) else 0
                            )
                            if items and isinstance(items, list) and items:
                                data["todo_first_item_preview"] = items[0].get(
                                    "text", ""
                                )[:200]
                        if kind == "latex":
                            data["latex_preview"] = content.get("latex", "")[:300]
                            data["title"] = content.get("title")
                        if kind == "chat":
                            data["chat_content_preview"] = content.get("content", "")[
                                :500
                            ]
                        content_str = json.dumps(content, ensure_ascii=False)
                        thought_markers = [
                            "<|channel|>thought",
                            "channel|>",
                            "<|channel|>",
                            "<|think|>",
                        ]
                        data["thought_leak_in_content"] = any(
                            m in content_str for m in thought_markers
                        )
                        data["thought_leak_markers"] = [
                            m for m in thought_markers if m in content_str
                        ]
                    else:
                        data["content"] = str(content)[:500]
                    data["kind_match"] = j.get("kind") == kind
                else:
                    data["json"] = None
                    data["is_json"] = False
            except Exception as e:
                data["json_error"] = f"{type(e).__name__}: {e}"
                data["raw_preview"] = r.text[:1000]
            if r.status_code == 422:
                try:
                    j = r.json()
                    data["422_detail"] = str(j)[:1000]
                    data["422_detail_str"] = json.dumps(j, ensure_ascii=False)[:1000]
                except:
                    pass
            log(f"6_output_{kind}", data)
            results[kind] = data
            await asyncio.sleep(0.3)
    return results


async def step7_searxng():
    data = {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5)
    ) as client:
        try:
            r = await client.get(
                f"{SEARXNG}/search", params={"q": "test", "format": "json"}
            )
            data["searxng_status"] = r.status_code
            data["searxng_headers"] = dict(r.headers)
            data["searxng_text_preview"] = r.text[:800]
            try:
                j = r.json()
                data["searxng_json_keys"] = (
                    list(j.keys())
                    if isinstance(j, dict)
                    else f"list len {len(j)}"
                    if isinstance(j, list)
                    else str(type(j))
                )
                data["searxng_has_results"] = bool(
                    j.get("results") if isinstance(j, dict) else j
                )
                if isinstance(j, dict) and "results" in j:
                    data["searxng_results_count"] = (
                        len(j["results"]) if isinstance(j["results"], list) else 0
                    )
            except Exception as e:
                data["searxng_json_error"] = f"{type(e).__name__}: {e}"
            data["searxng_alive"] = r.status_code == 200
        except Exception as e:
            data["searxng_error"] = f"{type(e).__name__}: {e}"
            data["searxng_alive"] = False
        if not data.get("searxng_alive"):
            try:
                r2 = await client.get(SEARXNG, params={"q": "test"})
                data["searxng_root_status"] = r2.status_code
                data["searxng_root_preview"] = r2.text[:500]
            except Exception as e:
                data["searxng_root_error"] = f"{type(e).__name__}: {e}"
    log("7_searxng", data)
    return data


async def step8_probe(sid):
    async with httpx.AsyncClient(timeout=TIMEOUT_LONG) as client:
        r = await client.post(f"{BASE}/api/sessions/{sid}/probe", json={})
        data = {"status": r.status_code, "text_preview": r.text[:1000]}
        try:
            j = r.json()
            data["json"] = j if isinstance(j, dict) else str(j)[:800]
            if isinstance(j, dict):
                data["keys"] = list(j.keys())
                qs = j.get("questions") or j.get("items") or j.get("probe") or []
                if isinstance(qs, list):
                    data["questions_count"] = len(qs)
                    if qs and isinstance(qs[0], dict):
                        data["first_q_preview"] = str(qs[0])[:400]
                        data["first_q_id"] = qs[0].get("id")
            elif isinstance(j, list):
                data["is_list"] = True
                data["count"] = len(j)
                if j:
                    data["first_preview"] = str(j[0])[:400]
        except Exception as e:
            data["json_error"] = f"{type(e).__name__}: {e}"
        log("8_probe", data)
        qid = None
        try:
            j = r.json()
            if isinstance(j, dict):
                qs = j.get("questions") or j.get("items") or []
                if qs and isinstance(qs[0], dict):
                    qid = qs[0].get("id")
            elif isinstance(j, list) and j and isinstance(j[0], dict):
                qid = j[0].get("id")
        except:
            pass
        if qid:
            r2 = await client.post(
                f"{BASE}/api/sessions/{sid}/quiz/{qid}/answer", json={"choice_index": 0}
            )
            data2 = {"answer_status": r2.status_code, "answer_text": r2.text[:800]}
            try:
                j2 = r2.json()
                data2["answer_json"] = str(j2)[:800]
                data2["answer_keys"] = (
                    list(j2.keys()) if isinstance(j2, dict) else "list"
                )
            except:
                pass
            log("8_answer", data2)
            data["answer_attempt"] = data2
        else:
            r3 = await client.post(
                f"{BASE}/api/sessions/{sid}/notes-quiz", json={"count": 2}
            )
            data3 = {
                "notes_quiz_status": r3.status_code,
                "notes_quiz_preview": r3.text[:800],
            }
            log("8_notes_quiz", data3)
            data["notes_quiz"] = data3
        return data


async def step9_stop_retry(sid):
    data = {}
    async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as client:
        try:
            r = await client.post(f"{BASE}/api/sessions/{sid}/stop")
            data["stop_status"] = r.status_code
            data["stop_text"] = r.text[:500]
            try:
                data["stop_json"] = r.json()
            except:
                pass
        except Exception as e:
            data["stop_error"] = f"{type(e).__name__}: {e}"
        try:
            r2 = await client.post(
                f"{BASE}/api/sessions/{sid}/retry",
                json={"client_msg_id": "m-nonexistent-123"},
            )
            data["retry_dummy_status"] = r2.status_code
            data["retry_dummy_text"] = r2.text[:800]
        except Exception as e:
            data["retry_error"] = f"{type(e).__name__}: {e}"
    log("9_stop_retry", data)
    return data


async def main():
    print("=== T33 live verification start ===")
    t_start = time.time()
    h = await step1_health()
    await asyncio.sleep(0.2)
    sid = await step2_sessions()
    if not sid:
        print("FAIL: could not create session")
        Path(".agents/jobs/T33.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8"
        )
        return
    await asyncio.sleep(0.2)
    await step3_files_watch()
    await asyncio.sleep(0.2)
    upload_res = await step4_upload()
    await asyncio.sleep(0.5)
    turn_res = await step5_turn(sid)
    await asyncio.sleep(0.5)
    outputs_res = await step6_outputs(sid)
    await asyncio.sleep(0.2)
    searxng_res = await step7_searxng()
    await asyncio.sleep(0.2)
    probe_res = await step8_probe(sid)
    await asyncio.sleep(0.2)
    stop_res = await step9_stop_retry(sid)
    passed_kinds = 0
    thought_leak_total = 0
    for kind, data in outputs_res.items():
        is_pass = (
            data.get("http_status") == 200
            and data.get("kind_match") == True
            and not data.get("thought_leak_in_content")
            and data.get("validation_attempts") in (1, 2)
        )
        if is_pass:
            passed_kinds += 1
        if data.get("thought_leak_in_content"):
            thought_leak_total += 1
    turn_thought_leak = evidence.get("5_turn", {}).get("thought_leak_count", 0)
    thought_leak_total += turn_thought_leak
    norm_pass = evidence.get("1_health", {}).get("normalization_pass", False)
    endpoint_reachable = h.get("endpoint_reachable") if isinstance(h, dict) else None
    model_configured = h.get("model_configured") if isinstance(h, dict) else None
    health_status = h.get("status") if isinstance(h, dict) else None
    web_search_summary = {
        "searxng_alive": searxng_res.get("searxng_alive"),
        "searxng_status": searxng_res.get("searxng_status"),
        "turn_meta_has_web": evidence.get("5_turn", {}).get("has_web_results_in_meta"),
        "teach_has_citations": outputs_res.get("teach", {}).get("citations_count"),
        "note": "graceful fallback expected if SearXNG unreachable",
    }
    turn_has_latex = evidence.get("5_turn", {}).get("has_latex")
    turn_ends_q = evidence.get("5_turn", {}).get("ends_with_question") or evidence.get(
        "5_turn", {}
    ).get("last_200_has_question")
    core_chat_pass = outputs_res.get("chat", {}).get("http_status") == 200
    turn_pass = (
        evidence.get("5_turn", {}).get("http_status") == 200
        and not turn_thought_leak
        and turn_has_latex
        and turn_ends_q
    )
    outputs_pass = passed_kinds >= 4
    no_leak = thought_leak_total == 0 and turn_thought_leak == 0
    overall_pass = (
        core_chat_pass
        and outputs_pass
        and no_leak
        and norm_pass
        and health_status in ("ok", "degraded")
        and model_configured
        and endpoint_reachable
    )
    summary = {
        "core_chat_pass": core_chat_pass,
        "turn_pass": turn_pass,
        "passed_kinds": passed_kinds,
        "outputs_pass": outputs_pass,
        "thought_leak_total": thought_leak_total,
        "no_leak": no_leak,
        "normalization_pass": norm_pass,
        "health_status": health_status,
        "model_configured": model_configured,
        "endpoint_reachable": endpoint_reachable,
        "turn_has_latex": turn_has_latex,
        "turn_ends_with_q": turn_ends_q,
        "web_search_summary": web_search_summary,
        "overall_pass": overall_pass,
        "elapsed_seconds": round(time.time() - t_start, 1),
    }
    evidence["summary"] = summary
    evidence["blockers"] = []
    if not overall_pass:
        if not norm_pass:
            evidence["blockers"].append(
                "BROT_BASE_URL normalization to https://ollama.com/v1 failed"
            )
        if not model_configured:
            evidence["blockers"].append("model_configured false")
        if not endpoint_reachable:
            evidence["blockers"].append("endpoint_reachable false")
        if not core_chat_pass:
            evidence["blockers"].append("core chat output failed")
        if not outputs_pass:
            evidence["blockers"].append(f"only {passed_kinds}/6 outputs passed, need 4")
        if not no_leak:
            evidence["blockers"].append(
                f"thought leakage detected total {thought_leak_total}"
            )
        if not turn_pass:
            evidence["blockers"].append(
                "turn did not pass (http or latex or question or leak)"
            )
    print(json.dumps(summary, indent=2))
    out_path = Path(".agents/jobs/T33.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final = {
        "task_id": "T33",
        "agent": "Merlin",
        "status": "done",
        "session_id": sid,
        "declared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "finalized_by": "Merlin",
        "summary": f"Live verification T33: health {health_status}, model_configured {model_configured}, reachable {endpoint_reachable}, normalization {norm_pass}, turn latex {turn_has_latex} q {turn_ends_q} leak {turn_thought_leak}, chat {core_chat_pass}, passed {passed_kinds}/6, overall {'PASS' if overall_pass else 'FAIL'}",
        "files": {"modified": [], "created": [".agents/jobs/T33.json"], "deleted": []},
        "base_commit": "unknown",
        "committed_at": None,
        "line_counts": {"before": {}, "after": {}},
        "modules_created": [],
        "documentation": [],
        "verification": "merlin",
        "notes": "live verification against http://192.168.1.57:8000 with BROT_BASE_URL bare https://ollama.com normalization check, SearXNG http://192.168.1.57:8080",
        "evidence": evidence,
        "checks": {
            "health": "PASS"
            if health_status == "ok" and model_configured and endpoint_reachable
            else "FAIL",
            "normalization": "PASS" if norm_pass else "FAIL",
            "session_create": "PASS" if sid else "FAIL",
            "turn_latex_socratic_no_leak": "PASS" if turn_pass else "FAIL",
            "outputs_4_of_6": "PASS" if outputs_pass else "FAIL",
            "no_thought_leak": "PASS" if no_leak else "FAIL",
            "searxng_wiring": "PASS"
            if web_search_summary.get("searxng_alive")
            else "DEGRADED (fallback ok if alive false but chat succeeded)",
        },
        "verdict": "verified" if overall_pass else "failed",
        "blockers": evidence["blockers"],
        "final_report": f"T33 live verification {'PASS' if overall_pass else 'FAIL'}: core chat {core_chat_pass}, {passed_kinds}/6 outputs, thought leak {thought_leak_total}, normalization {norm_pass}, health {health_status}/{model_configured}/{endpoint_reachable}, turn latex {turn_has_latex} socratic {turn_ends_q}, searxng alive {web_search_summary.get('searxng_alive')}",
    }
    out_path.write_text(
        json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {out_path}")
    print(final["final_report"])
    print(f"TASK STATUS: T33 -> {'verified' if overall_pass else 'failed'}")


if __name__ == "__main__":
    asyncio.run(main())
