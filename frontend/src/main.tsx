import {t as ui} from "./i18n";
import {Modal} from "./Modal";
import {usePreferences} from "./usePreferences";
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { api, type Project } from "./api";
import { ProjectManager } from "./ProjectManager";
import { ProjectTrashDialog } from "./ProjectTrashDialog";
import { ImportDialog } from "./ImportDialog";
import { AudioPlayer } from "./AudioPlayer";
import { JobPanel, type Job } from "./JobPanel";
import { issueReport, resultStamp } from "./resultInfo";
import { RequestLane } from "./requestLane";
import { ExportDialog } from "./ExportDialog";
import {ModelManager, type ManagedModel} from "./ModelManager";
import { BatchDialog } from "./BatchDialog";
import { TranslationPreferencesFields, type TranslationPreferences, type StyleVariants, type TargetLanguage } from "./TranslationPreferences";
import {GlossaryEditor} from "./GlossaryEditor";
import {GlobalGlossaryDialog} from "./GlobalGlossaryDialog";
import {readEntries, type TermEntry} from "./terminology";
import type { Cue, Details, TranscriptVersion, TranslationVersion, TrackView } from "./types";
import { ResultControls } from "./ResultControls";
import { ReviewPanel } from "./ReviewPanel";
import { CueEditor } from "./CueEditor";
import { useJobs } from "./useJobs";
import { liveJob } from "./jobState";
import { editRequest, dirtyDraft } from "./editorContract";
const time = (ms: number) => `${Math.floor(ms / 60000)
    .toString()
    .padStart(2, "0")}:${((ms / 1000) % 60).toFixed(2).padStart(5, "0")}`;
function App() {
    const {language,theme,setLanguage,setTheme}=usePreferences();
    const modelNames: Record<string, string> = {
        "whisper-ja-760m": "Whisper 760M", "cohere-asr-ja": "Cohere",
        "whisper-ja-anime-v0.3": "Whisper Anime v0.3", "whisper-ja-1.5b": "Whisper 1.5B",
        "qwen3.5-9b-review": "Qwen3.5 9B", "index-translate-9b": "Index 9B", "index-translate-2b": "Index 2B",
        "asmr-qwen3.5-9b": "ASMR Qwen 9B", "hy-mt2-7b": "Hy-MT2 7B", "hy-mt2-1.8b": "Hy-MT2 1.8B", legacy: ui("既有人工／機器譯文"),
    };
    const status: Record<string, string> = {
        queued: ui("排隊"),
        waiting: ui("等待 GPU"),
        running: ui("處理中"),
        completed: ui("完成"),
        cancelled: ui("已取消"),
        failed: ui("失敗"),
        interrupted: ui("已中斷"),
        stale: ui("待更新"),
        pending: ui("待翻譯"),
        translated: ui("已翻譯"),
        manual: ui("人工校對"),
        aligned: ui("已對齊"),
        asr: ui("ASR 初稿"),
        alignment_failed: ui("對齊失敗"),
    };
    const reviewFlagLabels: Record<string, string> = {
        alignment_native_fallback: ui("額外對齊結果不可靠，保留 Whisper 原生時間，請回聽確認"),
        alignment_zero_duration: ui("部分文字沒有獨立的有效時間，請回聽確認"),
        alignment_merged: ui("短句已與相鄰句段合併，保留原文與可定位的時間"),
        long_cue: ui("句段較長，請確認斷句"),
        low_asr_confidence: ui("辨識信心偏低，請回聽確認"),
        listen: ui("需要回聽"),
        translation_invalid_output: ui("機器譯文含格式碎片或模型標記，請重新翻譯"),
        translation_honorific_question: ui("舊版稱謂字串提示（新結果已停用，不能據此判定人物關係）"),
        translation_glossary_question: ui("術語可能需確認"),
        translation_residual_japanese: ui("譯文可能含未翻譯日文（也可能是姓名或引用）"),
        translation_repetition_question: ui("不同原文出現相同譯文，請確認對應"),
        translation_review_issue: ui("全文審校留下待確認疑點"),
    };
    const [defaultStyles, setDefaultStyles] = useState<StyleVariants>({"zh-Hant":"", "zh-Hans":""});
    const [defaultTerms, setDefaultTerms] = useState<TermEntry[]>([]);
    const [modelCatalog, setModelCatalog] = useState<ManagedModel[]>([]);
    const modelRequest = useRef(0);
    async function refreshModels() {
        const request = ++modelRequest.current;
        try {const value=await api<{models:ManagedModel[]}>("/models");if(request===modelRequest.current)setModelCatalog(value.models);} catch { /* Model manager displays fetch errors. */ }
    }
    useEffect(()=>{void refreshModels();const timer=setInterval(()=>void refreshModels(),5000);return()=>{clearInterval(timer);modelRequest.current++;};},[]);
    const [preferences, setPreferences] = useState<TranslationPreferences>({ target_language: "zh-Hant", translation_style: "" });
    const [projects, setProjects] = useState<Project[]>([]), [pid, setPid] = useState(localStorage.getItem("onsei:project") || ""), [loadedPid, setLoadedPid] = useState(""), [details, setDetails] = useState<Details | null>(null), [tid, setTid] = useState("");
    const [cues, setCues] = useState<Cue[]>([]), [jobs, setJobs] = useState<Job[]>([]), [selected, setSelected] = useState<string[]>([]), [active, setActive] = useState("");
    const [asr, setAsr] = useState("whisper-ja-760m"), [translation, setTranslation] = useState(localStorage.getItem("onsei:translation") || "index-translate-9b"), [error, setError] = useState(""), [busy, setBusy] = useState(false);
    const [filter, setFilter] = useState(""), [projectSearch, setProjectSearch] = useState(""), [onlyFlags, setOnlyFlags] = useState(false), [files, setFiles] = useState<string[]>([]), [dialog, setDialog] = useState("");
    const [trashTarget, setTrashTarget] = useState<Project | null>(null);
    const [playerExpanded, setPlayerExpanded] = useState(false);
    // Experiments require an explicit opt-in each session; old persisted choices
    // must not silently add inference stages to the standard translation baseline.
    const [afterReview, setAfterReview] = useState(false), [useReference, setUseReference] = useState(false), [reviewModel, setReviewModel] = useState(localStorage.getItem("ownsei:review-model") || "index-translate-9b");
    const [reportFull, setReportFull] = useState("");
    const [reportText, setReportText] = useState(""), [reportCopied, setReportCopied] = useState(false);
    const keybar = useRef<HTMLElement>(null);
    const [draft, setDraft] = useState<Cue | null>(null), [notes, setNotes] = useState(""), [glossary, setGlossary] = useState("{}"), [path, setPath] = useState(""), [title, setTitle] = useState("");
    const [versions, setVersions] = useState<TranscriptVersion[]>([]), [translationVersions, setTranslationVersions] = useState<TranslationVersion[]>([]), [extraAlignment, setExtraAlignment] = useState(false), [deleteTarget, setDeleteTarget] = useState<{
        pid: string;
        tid: string;
        kind: "versions" | "translations";
        id: string;
        label: string;
    } | null>(null), [now, setNow] = useState(0), [segmentEnd, setSegmentEnd] = useState<number | null>(null);
    const audio = useRef<HTMLAudioElement>(null), searchInput = useRef<HTMLInputElement>(null), rowRefs = useRef<Record<string, HTMLDivElement | null>>({});
    const base = `/projects/${pid}`;
    const [resultsLoading, setResultsLoading] = useState(false);
    const projectReads = useRef(new RequestLane());
    const resultReads = useRef(new RequestLane(setResultsLoading));
    const pendingActions = useRef(0);
    const selectionRef = useRef({ pid, tid });
    selectionRef.current = { pid, tid };
    const target = details?.settings.target_language || "zh-Hant";
    const projectPreferences: TranslationPreferences = {
        target_language: target,
        translation_style: details?.settings.translation_style || defaultStyles[target],
        translation_styles: {...defaultStyles, ...details?.settings.translation_styles},
    };
    function openStyle() {setPreferences(projectPreferences); setError(""); setDialog("style");}
    async function changeTarget(target_language: TargetLanguage) {
        projectReads.current.cancel();
        await api(base + "/settings", "PATCH", {target_language});
        await refresh();
    }
    const track = details?.tracks.find((t) => t.id === tid);
    const hasRunning = jobs.some((j) => liveJob(j.status));
    const activeTranslation = translationVersions.find((v) => v.active);
    const transcriptLabel = (v?: TranscriptVersion) => v
        ? `${modelNames[v.model || ""] || ui("匯入")}${v.extra_alignment ? ui(" + 對齊") : ""} · ${resultStamp(v.created_at, v.created_at_estimated)}` : ui("尚無轉錄");
    async function report(cue?: Cue) {
        if (!track)
            return;
        setReportCopied(false);
        const context = {
            projectTitle: details?.settings.title || pid, trackName: track.name,
            transcriptLabel: transcriptLabel(versions.find(v => v.version === track.active_version)),
            translationLabel: activeTranslation ? `${modelNames[activeTranslation.model] || activeTranslation.model} · ${activeTranslation.stage === "review" ? ui("審校稿") : ui("初譯")} · ${resultStamp(activeTranslation.created_at)}` : ui("尚無中文結果"),
            location: { project: pid, track: tid, transcript: track.active_version, translation: activeTranslation?.id || null, cue: cue?.id || null },
            cue,
        };
        const full = issueReport(context);
        setReportFull(full);
        setReportText(full);
        try {
            const result = await api<{
                code: string;
            }>("/locations/compact", "POST", context.location);
            setReportText(issueReport(context, { full: false, code: result.code }));
        }
        catch { /* Full IDs remain available if a concurrent deletion invalidates the short reference. */ }
    }
    const guard = async (fn: () => Promise<void>) => {
        const owner = { ...selectionRef.current };
        pendingActions.current += 1;
        setBusy(true);
        setError("");
        try {
            await fn();
        }
        catch (e) {
            if (selectionRef.current.pid === owner.pid && selectionRef.current.tid === owner.tid)
                setError(String(e));
        }
        finally {
            pendingActions.current -= 1;
            setBusy(pendingActions.current > 0);
        }
    };
    async function refreshProjects() {
        const next = await api<Project[]>("/projects");
        setProjects(next);
        if (selectionRef.current.pid &&
            !next.some((p) => p.id === selectionRef.current.pid)) {
            selectProject("");
        }
    }
    function selectProject(id: string) {
        if (id === selectionRef.current.pid)
            return;
        if (!discardDraft())
            return;
        audio.current?.pause();
        projectReads.current.cancel();
        resultReads.current.cancel();
        selectionRef.current = { pid: id, tid: "" };
        setPid(id);
        setError("");
        setDetails(null);
        setLoadedPid("");
        setTid("");
        setCues([]);
        setJobs([]);
        setFiles([]);
        setVersions([]);
        setTranslationVersions([]);
        setDeleteTarget(null);
        setTrashTarget(null);
        setPlayerExpanded(false);
        setNow(0);
        setFilter("");
        setOnlyFlags(false);
        setActive("");
        setSelected([]);
        setDraft(null);
        setSegmentEnd(null);
    }
    async function refresh() {
        if (!pid || selectionRef.current.pid !== pid)
            return;
        await projectReads.current.run(signal => api<Details>(base, "GET", undefined, { signal }), d => {
            if (selectionRef.current.pid !== pid)
                return;
            setDetails(d);
            setLoadedPid(pid);
            setJobs(d.jobs);
            const current = selectionRef.current.tid;
            if (!current || !d.tracks.some((t) => t.id === current)) {
                const stored = localStorage.getItem(`onsei:track:${pid}`);
                const next = d.tracks.find((t) => t.id === stored)?.id || d.tracks[0]?.id || "";
                setTid(next);
            }
        });
    }
    function applyView(view: TrackView) {
        if (selectionRef.current.pid !== pid || selectionRef.current.tid !== view.track.id)
            return;
        setDetails(current => current ? { ...current, tracks: current.tracks.map(t => t.id === view.track.id ? view.track : t) } : current);
        setCues(view.cues);
        setVersions(view.versions);
        setTranslationVersions(view.translations);
        setFiles(view.files);
    }
    async function refreshCues() {
        if (selectionRef.current.pid !== pid || selectionRef.current.tid !== tid)
            return;
        if (!pid ||
            !tid ||
            loadedPid !== pid ||
            !details?.tracks.some((t) => t.id === tid)) {
            setCues([]);
            return;
        }
        await resultReads.current.run(signal => api<TrackView>(base + `/tracks/${tid}/view`, "GET", undefined, { signal }), applyView);
    }
    const refreshRef = useRef({ refresh, refreshCues });
    refreshRef.current = { refresh, refreshCues };
    useEffect(() => {
        refreshProjects().catch((e) => setError(String(e)));
        void api<{
            translation_styles: StyleVariants; glossary_entries:TermEntry[];
        }>("/preferences/defaults").then(d => {setDefaultStyles(d.translation_styles); setDefaultTerms(d.glossary_entries);}).catch(e => setError(String(e)));
    }, []);
    useEffect(() => {
        if (!keybar.current)
            return;
        const observer = new ResizeObserver(() => {
            if (keybar.current)
                document.documentElement.style.setProperty("--keybar-height", `${keybar.current.getBoundingClientRect().height}px`);
        });
        observer.observe(keybar.current);
        return () => observer.disconnect();
    }, []);
    useEffect(() => {
        setNow(0);
        setSegmentEnd(null);
        setPlayerExpanded(false);
        setCues([]);
        setFiles([]);
    }, [pid, tid]);
    useEffect(() => {
        if (pid)
            localStorage.setItem("onsei:project", pid);
        else
            localStorage.removeItem("onsei:project");
        setTid("");
        setActive("");
        setSelected([]);
        setDraft(null);
        refresh().catch(e => { if (selectionRef.current.pid === pid)
            setError(String(e)); });
        return () => projectReads.current.cancel();
    }, [pid]);
    useEffect(() => {
        setActive("");
        setSelected([]);
        setDraft(null);
        setVersions([]);
        setTranslationVersions([]);
        refreshCues().catch(e => {
            if (selectionRef.current.pid === pid && selectionRef.current.tid === tid)
                setError(String(e));
        });
        return () => resultReads.current.cancel();
    }, [pid, tid, loadedPid]);
    useJobs(base, !!pid, { onJobs: setJobs, onError: e => setError(String(e)), onResultsChanged: async () => {
            if (pendingActions.current)
                return false;
            await refreshRef.current.refresh();
            if (pendingActions.current)
                return false;
            await refreshRef.current.refreshCues();
            return true;
        } });
    function switchResult(kind: "versions" | "translations", id: string) {
        if (!discardDraft())
            return;
        guard(async () => {
            projectReads.current.cancel();
            resultReads.current.cancel();
            const view = await api<TrackView>(base + `/tracks/${tid}/${kind}/${id}`, "POST");
            if (selectionRef.current.pid !== pid || selectionRef.current.tid !== tid)
                return;
            if (kind === "translations") {
                const model = translationVersions.find(v => v.id === id)?.model;
                if (model && model !== "legacy") {
                    setTranslation(model);
                    localStorage.setItem("onsei:translation", model);
                }
            }
            setActive("");
            setSelected([]);
            applyView(view);
        });
    }
    function selectTrack(id: string) {
        if (id === tid || !details?.tracks.some(t => t.id === id) || !discardDraft())
            return;
        audio.current?.pause();
        resultReads.current.cancel();
        selectionRef.current = { pid, tid: id };
        setTid(id);
        setActive("");
        setSelected([]);
        localStorage.setItem(`onsei:track:${pid}`, id);
    }
    async function start(action: string, all = false, local = false) {
        await api(base + "/jobs", "POST", {
            action,
            asr,
            translation,
            track_ids: all ? null : [tid],
            cue_ids: local ? selected : null,
            extra_alignment: extraAlignment,
            after_review: afterReview && !local,
            use_reference: useReference,
            review_model: reviewModel,
        });
        await refresh();
    }
    const originalDraft = useRef<Cue | null>(null);
    const draftDirty = dirtyDraft(originalDraft.current, draft);
    function discardDraft() {
        if (draftDirty && !window.confirm(ui("目前有未儲存的校對，確定放棄這些修改嗎？")))
            return false;
        originalDraft.current = null;
        setDraft(null);
        return true;
    }
    function choose(c: Cue) {
        if (draft?.id === c.id)
            return;
        if (!discardDraft())
            return;
        originalDraft.current = structuredClone(c);
        setActive(c.id);
        setDraft(structuredClone(c));
    }
    function play(c: Cue, full = false) {
        if (audio.current) {
            audio.current.currentTime = Math.max(0, c.start_ms / 1000 - 0.2);
            setSegmentEnd(full ? null : c.end_ms / 1000 + 0.2);
            audio.current.play().catch((e) => setError(String(e)));
        }
        choose(c);
    }
    async function save() {
        if (!draft)
            return;
        const original = originalDraft.current;
        if (!original)
            return;
        await api(base + `/cues/${draft.id}`, "PATCH", editRequest(original, draft));
        await Promise.all([refreshCues(), refresh()]);
        originalDraft.current = null;
        setDraft(null);
    }
    useEffect(() => {
        const listener = (e: KeyboardEvent) => {
            const modalOpen = !!dialog || !!deleteTarget || !!trashTarget || !!reportText || playerExpanded;
            if (e.ctrlKey && e.key === "Enter" && draft && !modalOpen) {
                e.preventDefault();
                guard(save);
            }
            const typing = e.target instanceof Element && e.target.closest("input,textarea,select,[contenteditable=true]");
            if (e.key === "/" && !typing && !modalOpen) {
                e.preventDefault();
                searchInput.current?.focus();
            }
            if (e.key === "Escape" && reportText) {
                setReportText("");
                return;
            }
            if (e.key === "Escape" && dialog !== "import" && !trashTarget && !playerExpanded) {
                if (discardDraft()) {
                    setDialog("");
                    setDeleteTarget(null);
                    setActive("");
                }
            }
            if (e.code === "Space" &&
                !modalOpen &&
                !typing && !(e.target instanceof Element && e.target.closest("button,a")) &&
                audio.current) {
                e.preventDefault();
                if (audio.current.paused)
                    audio.current.play().catch(e => setError(String(e)));
                else
                    audio.current.pause();
            }
        };
        window.addEventListener("keydown", listener);
        return () => window.removeEventListener("keydown", listener);
    }, [draft, cues, dialog, deleteTarget, trashTarget, reportText, playerExpanded]);
    useEffect(() => {
        if (!draftDirty)
            return;
        const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, [draftDirty]);
    const visible = cues.filter((c) => (!onlyFlags ||
        c.review_flags.length > 0 ||
        c.timing_status === "stale" ||
        c.timing_status === "alignment_failed" ||
        c.translation_status === "stale" ||
        c.translation_status === "pending" ||
        c.translation_status === "failed") &&
        (!filter || c.ja.includes(filter) || c.zh?.includes(filter)));
    return (<div className={"app" + (track ? " has-player" : "")}>
      <aside inert={playerExpanded || !!dialog || !!deleteTarget || !!trashTarget || !!reportText}>
        <div className="brand">
          <div className="brandmark">OW</div>
          <div>
            <b>Ownsei</b>
            <small>{ui("本機字幕工作台")}</small>
          </div>
        </div>
        <div className="section-label">{ui("作品 /")}{projects.length}{" "}
          <button className="icon" aria-label={ui("建立作品")} onClick={() => {
            setDialog("create");
            setTitle("");
            setPath("");
        }}>{ui("[新增]")}</button>
        </div>
        <input className="project-search" aria-label={ui("搜尋側欄作品")} placeholder={ui("篩選作品…")} value={projectSearch} onChange={e => setProjectSearch(e.target.value)}/>
        <nav aria-label={ui("作品列表")}>
          {projects.filter(p => p.title.toLowerCase().includes(projectSearch.toLowerCase())).map((p) => (<div className={"project-row " + (p.id === pid ? "current" : "")} key={p.id}>
              <button className="project" aria-label={p.title} aria-current={p.id === pid ? "true" : undefined} title={p.title} onClick={() => selectProject(p.id)}>
                <span className="disc">{p.id === pid ? ">" : "·"}</span>
                <span>{p.title}<small>{p.tracks}{ui(" 條音軌")}</small></span>
              </button>
              <button className="project-delete" aria-label={ui("刪除「") + (p.title) + "」"} title={ui("刪除作品")} disabled={busy} onClick={() => setTrashTarget(p)}>×</button>
            </div>))}
        </nav>
        <button className="manage-projects" aria-label={ui("作品管理")} title={ui("作品管理")} onClick={() => setDialog("manage")}>
          <span>{ui("作品管理")}</span>
        </button>
        <button className="manage-projects" disabled={busy} onClick={() => setDialog("global")}>{ui("全局術語表")}</button>
        <button className="manage-projects" onClick={()=>setDialog("models")}>{ui("模型管理")}</button>
        <div className="appearance-settings"><label>{ui("介面語言")}<select value={language} onChange={e=>setLanguage(e.target.value as "zh-Hant"|"zh-Hans")}><option value="zh-Hant">繁體中文</option><option value="zh-Hans">简体中文</option></select></label>
          <label>{ui("配色主題")}<select value={theme} onChange={e=>setTheme(e.target.value)}><option value="classic">{ui("經典工作台")}</option><option value="mocha">Catppuccin Mocha</option></select></label></div>
        <div className="sidebar-foot">
          <span className="dot"/>{ui("本機工作空間")}<small>{ui("日文原文 → 中文字幕")}<br />{ui("CUDA · 分階段推論")}</small>
        </div>
      </aside>
      <main inert={playerExpanded || !!dialog || !!deleteTarget || !!trashTarget || !!reportText}>
        <header>
          <div>
            <div className="eyebrow">OWNSEI / LOCAL WORKSPACE</div>
            <h1>{details?.settings.title || ui("日文轉錄與中文字幕")}</h1>
            <p>
              {details
            ? ui("原音回聽、雙語校對與字幕匯出") : ui("建立作品，匯入音檔，開始你的第一份雙語逐字稿。")}
            </p>
          </div>
          {pid && (<div className="header-actions">
              <button onClick={() => {
                setDialog("settings");
                setNotes(details?.settings.notes || "");
                setGlossary(JSON.stringify(details?.settings.glossary_entries || details?.settings.glossary || {}, null, 2));
            }}>{ui("作品設定")}</button>
              <button onClick={() => {
                setDialog("import");
                setPath("");
            }}>{ui("匯入音檔")}</button>
              <button className="primary" disabled={busy || !tid} onClick={() => { if (discardDraft())
            setDialog("batch"); }}>{ui("批量處理")}</button>
            </div>)}
        </header>
        {error && (<div className="error" role="alert">
            {error}
            <button className="icon" onClick={() => setError("")}>
              ×
            </button>
          </div>)}
        {!pid ? (<div className="welcome">
            <h2>{ui("[ 尚未選擇作品 ]")}</h2>
            <p>{ui("從本機音檔開始。轉錄、翻譯與人工校對各自保存，")}<br />{ui("隨時可以接著上次的進度。")}</p>
            <button className="primary" onClick={() => {
                setDialog("create");
                setTitle("");
                setPath("");
            }}>{ui("建立第一個作品")}</button>
          </div>) : (<>
            <div className="workspace-controls">
              <section className="toolbar">
                <h2 className="panel-title">{ui("處理設定")}</h2>
                <label>{ui("音軌")}<select aria-label={ui("音軌")} value={tid} disabled={busy} onChange={(e) => {
                selectTrack(e.target.value);
            }}>
                    {details?.tracks.map((t) => (<option key={t.id} value={t.id}>
                        {t.name} · {time(t.duration_ms)}
                      </option>))}
                  </select>
                </label>
                <label>{ui("日文轉錄")}<select value={asr} onChange={(e) => setAsr(e.target.value)}>
                    <option value="whisper-ja-760m">Whisper JA · 760M</option>
                    <option value="whisper-ja-anime-v0.3">Whisper JA Anime · v0.3 INT8</option>
                    <option value="whisper-ja-1.5b">Whisper JA · 1.5B INT8</option>
                    <option value="cohere-asr-ja">Cohere ASR · JA</option>
                  </select>
                </label>
                {modelCatalog.some(m=>!m.ready && [asr,translation,...((extraAlignment||asr==="cohere-asr-ja")?["aligner"]:[])].includes(m.id)) && <p className="error">{ui("尚未準備的模型：")}{modelCatalog.filter(m=>!m.ready && [asr,translation,...((extraAlignment||asr==="cohere-asr-ja")?["aligner"]:[])].includes(m.id)).map(m=>m.name).join("、")} · {ui("需要使用時，可在模型管理準備。")}</p>}
                <label>{ui("中文翻譯")}<select value={translation} onChange={(e) => { setTranslation(e.target.value); localStorage.setItem("onsei:translation", e.target.value); }}>
                    <option value="index-translate-9b">Index Translate · 9B Q4</option>
                    <option value="asmr-qwen3.5-9b">{ui("ASMR Qwen3.5 · 9B Q4")}</option>
                    <option value="index-translate-2b">{ui("Index Translate · 2B Q6（快速驗證）")}</option>
                    <option value="hy-mt2-7b">Hy-MT2 · 7B Q4</option>
                    <option value="hy-mt2-1.8b">Hy-MT2 · 1.8B Q6</option>
                  </select>
                </label>
                <div className="module-actions">
                  <label className="check" title={ui("使用 Qwen3 ForcedAligner 另存一份對齊結果，保留 Whisper 原生時間版本；準確度仍需回聽確認")}>
                    <input type="checkbox" checked={extraAlignment} disabled={!asr.startsWith("whisper-ja-")} onChange={(e) => setExtraAlignment(e.target.checked)}/>{ui("額外使用 Qwen3 ForcedAligner 進行對齊")}</label>
                  {asr === "cohere-asr-ja" && <small>{ui("Cohere 無原生時間戳，使用 Qwen3 ForcedAligner 對齊")}</small>}
                  <button disabled={!tid || busy} onClick={() => guard(() => start("transcribe"))}>{ui("轉錄此音軌")}</button>
                  <button disabled={!cues.length || busy} title={ui("另存一份初譯，保留所選來源稿的人工校對")} onClick={() => guard(() => start("translate"))}>{ui("翻譯")}</button>
                  <label className="translation-target">{ui("字幕語言")}<select aria-label={ui("字幕目標語言")} value={target} disabled={busy || !defaultStyles[target]} onChange={e => guard(() => changeTarget(e.target.value as TargetLanguage))}>
                    <option value="zh-Hant">{ui("繁體中文")}</option><option value="zh-Hans">{ui("简体中文")}</option>
                  </select></label>
                  <button disabled={busy || !defaultStyles[target]} onClick={openStyle}>{ui("翻譯風格指引")}</button>
                </div>
                <small className="translation-baseline-note">{ui("標準翻譯：日文與前後文 → 分批翻譯 → 格式檢查與字幕輸出。作品術語優先於全局術語，依字幕目標語言轉換繁簡")}</small>
                <details className="translation-experiments">
                  <summary>{ui("實驗性翻譯功能")}{(useReference || afterReview) && <b className="experiment-active">{ui("已啟用：")}{[useReference && ui("全文參照"), afterReview && ui("審校")].filter(Boolean).join("、")}</b>}</summary>
                  <p>{ui("這些額外步驟尚未證明能穩定改善跨作品品質，可能保留錯譯或引入誤判，並增加處理時間。每次開啟介面預設關閉；既有結果仍保留")}</p>
                  <div className="module-actions">
                    <label className="check"><input type="checkbox" checked={useReference} onChange={e => setUseReference(e.target.checked)}/>{ui("整理全文參照")}</label>
                    <label className="check"><input type="checkbox" checked={afterReview} onChange={e => setAfterReview(e.target.checked)}/>{ui("翻譯後全文審校（另存審校稿）")}</label>
                    <label>{ui("審校模型")}<select value={reviewModel} onChange={e => { setReviewModel(e.target.value); localStorage.setItem("ownsei:review-model", e.target.value); }}>
                      <option value="qwen3.5-9b-review">{ui("Qwen3.5 9B · 實驗")}</option><option value="index-translate-9b">Index 9B</option><option value="asmr-qwen3.5-9b">ASMR Qwen 9B</option>
                      <option value="index-translate-2b">{ui("Index 2B（流程驗證）")}</option>
                    </select></label>
                    <button disabled={!activeTranslation || activeTranslation.status === "stale" || (activeTranslation.stage === "review" && activeTranslation.status !== "completed") || !cues.length || busy || hasRunning || cues.some(c => !c.zh || ["pending", "stale", "failed"].includes(c.translation_status))} onClick={() => guard(() => start("review"))}>{ui("另建全文審校稿")}</button>
                  </div>
                </details>
              </section>
              <ResultControls track={track} versions={versions} translations={translationVersions} busy={busy} running={hasRunning} loading={resultsLoading} models={modelNames} label={transcriptLabel} time={time} onSwitch={switchResult} onDelete={(kind, id, label) => { if (discardDraft())
            setDeleteTarget({ pid, tid, kind, id, label }); }} onReload={() => guard(async () => { await refresh(); await refreshCues(); })} onReport={() => report()}/>
              <ReviewPanel base={base} trackId={tid} result={activeTranslation} cues={cues}/>
            </div>
            <JobPanel base={base} jobs={jobs} busy={busy} onAction={(id, action) => guard(async () => {
                await api(base + `/jobs/${id}/${action}`, "POST");
                await refresh();
            })}/>
            <section className="editor-tools">
              <div className="tabs">
                <b>{ui("雙語逐字稿")}</b>
                <span>{visible.length} / {cues.length}{ui(" 句")}</span>
                <span>{ui("疑點 ")}{cues.filter(c => c.review_flags.length > 0).length}</span>
                <span>{ui("時間待處理 ")}{cues.filter(c => ["stale", "alignment_failed"].includes(c.timing_status)).length}</span>
                <span>{ui("譯文待處理 ")}{cues.filter(c => ["pending", "stale", "failed"].includes(c.translation_status)).length}</span>
              </div>
              <div className="search-tools">
                <input ref={searchInput} aria-label={ui("搜尋台詞")} placeholder={ui("搜尋日文或中文…")} value={filter} onChange={(e) => setFilter(e.target.value)}/>
                <label className="check">
                  <input type="checkbox" checked={onlyFlags} onChange={(e) => setOnlyFlags(e.target.checked)}/>{ui("只看疑點／待更新")}</label>

              </div>
            </section>
            {selected.length > 0 && (<div className="selection">
                <span>{ui("已選 ")}{selected.length}{ui(" 句")}</span>
                <button onClick={() => guard(() => start("realign", false, true))}>{ui("重新對齊所在區塊")}</button>
                <button onClick={() => guard(() => start("translate", false, true))}>{ui("重譯相關批次")}</button>
                <button onClick={() => setSelected([])}>{ui("清除選取")}</button>
              </div>)}
            <div className="table-head">
              <span>{ui("時間 / 選取")}</span>
              <span>{ui("日文原文")}</span>
              <span>{ui("中文譯文")}{activeTranslation?.target_language === "zh-Hans" ? ui("（簡體）") : activeTranslation?.target_language === "zh-Hant" ? ui("（繁體）") : ""}</span>
              <span>{ui("狀態")}</span>
            </div>
            {draft && !cues.some(c => c.id === draft.id) && <div className="error" role="alert">{ui("結果已在其他操作中切換，未保存草稿仍保留；請複製後重新開啟需要校對的結果。")}<textarea aria-label={ui("保留的日文草稿")} readOnly value={draft.ja}/>
              <textarea aria-label={ui("保留的中文草稿")} readOnly value={draft.zh || ""}/>
              <button onClick={() => discardDraft()}>{ui("關閉草稿")}</button>
            </div>}
            <section className="transcript">
              {visible.length ? (visible.map((c) => (<div key={c.id} ref={(el) => {
                    rowRefs.current[c.id] = el;
                }} className={"cue " +
                    (active === c.id ? "active" : "") +
                    " " +
                    (now * 1000 >= c.start_ms && now * 1000 < c.end_ms
                        ? "playing"
                        : "")}>
                    <div className="cue-time">
                      <label>
                        <input type="checkbox" checked={selected.includes(c.id)} onChange={(e) => setSelected(e.target.checked
                    ? [...selected, c.id]
                    : selected.filter((id) => id !== c.id))}/>
                        <span>#{c.ordinal + 1}</span>
                      </label>
                      <button className="time-link" onClick={() => play(c)}>
                        {time(c.start_ms)}
                        <small>{time(c.end_ms)} ▷</small>
                      </button>
                    </div>
                    <div className="ja" lang="ja" onClick={() => choose(c)}>
                      {c.ja}
                      {c.ja_override !== null && (<small className="edited">{ui("已校對日文")}</small>)}
                    </div>
                    <div className="zh" onClick={() => choose(c)}>
                      {c.zh || <span className="muted">{ui("等待翻譯")}</span>}
                    </div>
                    <div className="cue-status">
                      <span className={"badge " + c.translation_status}>
                        {status[c.translation_status]}
                      </span>
                      <span className={"badge timing " + c.timing_status}>{status[c.timing_status] || c.timing_status}</span>
                      {c.review_flags.length > 0 && (<span className="flag" title={c.review_flags
                        .map((flag) => reviewFlagLabels[flag] || flag)
                        .join("；")}>
                          {c.review_flags.includes("alignment_zero_duration")
                        || c.review_flags.includes("alignment_native_fallback") ? ui("! 時間需回聽") : ui("! 疑點")}
                        </span>)}
                      <button onClick={() => choose(c)}>{ui("編輯")}</button>
                      <button aria-label={ui("回報句段 #") + (c.ordinal + 1) + ""} onClick={() => report(c)}>{ui("回報")}</button>
                    </div>
                    {active === c.id && draft?.id === c.id && <CueEditor cue={c} draft={draft} busy={busy} changed={originalDraft.current?.revision !== c.revision} onChange={setDraft} onSave={() => guard(save)} onClose={() => { if (discardDraft())
                    setActive(""); }} onReset={() => guard(async () => {
                        if (draftDirty && !window.confirm(ui("確定放棄草稿並恢復模型文字嗎？")))
                            return;
                        const original = originalDraft.current;
                        if (!original)
                            return;
                        await api(base + `/cues/${original.id}`, "PATCH", { revision: original.revision,
                            translation_version: original.provenance.translation_version ?? null, changes: { ja_override: null, zh_override: null } });
                        originalDraft.current = null;
                        setDraft(null);
                        await refreshCues();
                    })}/>}
                  </div>))) : (<div className="empty">
                  <h3>{track ? ui("還沒有逐字稿") : ui("先匯入一條音軌")}</h3>
                  <p>
                    {track
                    ? ui("開始轉錄後，日文與譯文會出現在這裡。") : ui("支援 WAV、MP3、FLAC、M4A、OGG；可匯入單檔或資料夾。")}
                  </p>
                </div>)}
            </section>
            <section className="exports"><div><h3>{ui("字幕與逐字稿")}</h3><p>{ui("選擇音軌、來源版本、語言與格式；VTT 為預設格式")}</p></div>
              <button disabled={busy || !details?.tracks.some(t => t.active_version)} onClick={() => { if (discardDraft())
            setDialog("export"); }}>{ui("匯出字幕／逐字稿")}</button>
              <details><summary>{ui("既有有效匯出檔案（")}{files.length}）</summary><div className="file-list">{files.map(f => <a key={f} href={"/api" + base + "/exports/" + encodeURIComponent(f)} download>{f}</a>)}</div></details>
            </section>
          </>)}
      </main>
      <footer className="keybar" ref={keybar} inert={playerExpanded || !!dialog || !!deleteTarget || !!trashTarget || !!reportText}><span>{ui("本機 / CUDA")}</span><span><kbd>Space</kbd>{ui(" 播放／暫停")}</span>
        <span><kbd>/</kbd>{ui(" 搜尋台詞")}</span><span><kbd>Ctrl Enter</kbd>{ui(" 儲存校對")}</span><span><kbd>Esc</kbd>{ui(" 收起／關閉")}</span></footer>
      {track && <div inert={!!dialog || !!deleteTarget || !!trashTarget || !!reportText}><AudioPlayer key={`${pid}:${tid}`} track={track} source={"/api" + base + `/tracks/${tid}/audio`} projectTitle={details?.settings.title || ""} versionLabel={`${modelNames[versions.find(v => v.version === track.active_version)?.model || ""] || ui("日文")} / ${activeTranslation ? modelNames[activeTranslation.model] || activeTranslation.model : ui("尚無中文譯文")}`} cues={cues} audioRef={audio} now={now} onTime={setNow} segmentEnd={segmentEnd} onSegmentEnd={setSegmentEnd} tracks={details?.tracks || []} onTrack={selectTrack} switchingDisabled={busy} expanded={playerExpanded} onExpanded={setPlayerExpanded}/></div>}
      {trashTarget && <ProjectTrashDialog project={trashTarget} onClose={() => setTrashTarget(null)} onDeleted={async () => { await refreshProjects(); setTrashTarget(null); }}/>}
      {reportText && <div className="modal-backdrop">
        <section className="modal issue-report" role="dialog" aria-modal="true" aria-label={ui("問題定位資訊")}>
          <h2>{ui("回報問題")}</h2>
          <p>{ui("複製這份資訊並附在問題回報中，再補充實際遇到的問題。已包含作品、音軌、目前日文／中文結果與句段定位；不會自動傳送。")}</p>
          <textarea aria-label={ui("問題定位內容")} readOnly value={reportText} onFocus={e => e.currentTarget.select()}/>
          <div className="manager-actions">
            <button onClick={() => setReportText("")}>{ui("關閉")}</button>
            <button onClick={() => void navigator.clipboard.writeText(reportFull).then(() => setReportCopied(true)).catch(() => setReportCopied(false))}>{ui("複製完整診斷")}</button>
            <button onClick={() => void navigator.clipboard.writeText(reportText).then(() => setReportCopied(true)).catch(() => setReportCopied(false))}>{ui("複製定位資訊")}</button>
          </div>
          <small role="status">{reportCopied ? ui("已複製，可直接貼上回報") : ui("若無法複製，可點選上方內容後按 Ctrl+C")}</small>
        </section>
      </div>}
      {deleteTarget && <div className="modal-backdrop">
        <section className="modal" role="dialog" aria-modal="true" aria-label={ui("刪除結果確認")}>
          <h2>{ui("刪除 ")}{deleteTarget.label} {deleteTarget.kind === "versions" ? ui("轉錄") : ui("譯文")}？</h2>
          <p>{deleteTarget.kind === "versions"
                ? ui("會移除此轉錄及其所有模型的中文結果，包含這些版本的人工校對。") : ui("會移除這份中文結果及其中的人工中文校對；其他初譯與審校稿保留，包含由這份結果建立的審校稿。")}</p>
          <p>{ui("原始音檔保留。刪除資料會備份到作品資料夾的 trash；下次處理會重新推論。")}</p>
          <div className="manager-actions">
            <button disabled={busy} onClick={() => setDeleteTarget(null)}>{ui("取消")}</button>
            <button className="danger" disabled={busy || hasRunning} onClick={() => guard(async () => {
                await api(`/projects/${deleteTarget.pid}/tracks/${deleteTarget.tid}/${deleteTarget.kind}/${deleteTarget.id}`, "DELETE");
                setDeleteTarget(null);
                setDraft(null);
                setActive("");
                setSelected([]);
                await refresh();
                await refreshCues();
            })}>{ui("確認刪除")}</button>
          </div>
        </section>
      </div>}
      {dialog === "global" ? (<GlobalGlossaryDialog defaults={defaultTerms} onClose={() => setDialog("")}/>) : dialog === "style" ? (<Modal title={ui("翻譯風格指引")} onClose={() => setDialog("")} busy={busy} className="style-dialog">
          <p>{ui("正在編輯：")}{preferences.target_language === "zh-Hans" ? ui("简体中文") : ui("繁體中文")}</p>
          <TranslationPreferencesFields value={preferences} onChange={setPreferences} defaultStyles={defaultStyles} disabled={busy} showTarget={false}/>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="manager-actions"><button disabled={busy} onClick={() => setDialog("")}>{ui("取消")}</button><button className="primary" disabled={busy || !preferences.translation_style.trim()} onClick={() => guard(async () => {
            await api(base + "/settings", "PATCH", {translation_styles: {[preferences.target_language]: preferences.translation_style}});
            await refresh(); setDialog("");
          })}>{ui("儲存風格指引")}</button></div>
        </Modal>) : dialog === "manage" ? (<ProjectManager onClose={() => setDialog("")} onOpen={(id) => {
                selectProject(id);
                setDialog("");
            }} onChanged={async () => {
                await refreshProjects();
                if (selectionRef.current.pid === pid)
                    await refresh();
            }}/>) : dialog === "models" ? (<ModelManager onClose={()=>setDialog("")} onChanged={()=>void refreshModels()}/>) : dialog === "export" ? (<ExportDialog key={base} base={base} tracks={details?.tracks || []} current={tid} models={modelNames} onClose={() => setDialog("")}/>) : dialog === "batch" ? (<BatchDialog key={base} base={base} tracks={details?.tracks || []} current={tid} asr={asr} translation={translation} extraAlignment={extraAlignment} models={modelNames} defaultStyles={defaultStyles} preferences={projectPreferences} onClose={() => setDialog("")} onSubmitted={refresh}/>) : dialog === "import" ? (<ImportDialog key={base} base={base} onClose={() => setDialog("")} onChanged={async () => { await refresh(); await refreshProjects(); }}/>) : dialog && (<Modal title={dialog==="create"?ui("建立作品"):ui("作品設定")} onClose={()=>setDialog("")} busy={busy}>
            {error&&<p className="error" role="alert">{error}</p>}
            {dialog === "create" ? (<>
                <label>{ui("作品標題")}<input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder={ui("作品名稱")}/>
                </label>
                <label>{ui("已有 CLI 專案路徑（選填）")}<input value={path} onChange={(e) => setPath(e.target.value)} placeholder={ui("留空則建立新專案")}/>
                </label>
                <button className="primary" disabled={busy || !title.trim()} onClick={() => guard(async () => {
                    const p = await api<{
                        id: string;
                    }>("/projects", "POST", {
                        title,
                        path: path || null,
                        target_language: language,
                    });
                    await refreshProjects();
                    selectProject(p.id);
                    setDialog("");
                })}>{ui("建立 / 開啟")}</button>
              </>) : (<>
                <label>{ui("角色、稱謂與場景備註")}<textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={ui("例如：說話者：學妹。聽者：學長。旁白：無。學長沒有配音；學妹直接向學長問話、調侃。只填已知設定。")}/>
                </label>
                <GlossaryEditor value={glossary} onChange={setGlossary} scope="project" disabled={busy}/>
                <small>{ui("每批最多 24 句，附前後各 6 句日文、音軌名稱和此處設定。修改後會標記機器譯文待更新，人工譯文保留。")}</small>
                <button className="primary" disabled={busy} onClick={() => guard(async () => {
                    await api(base + "/settings", "PATCH", {
                        notes,
                        glossary_entries: readEntries(glossary),
                    });
                    await refresh();
                    await refreshCues();
                    setDialog("");
                })}>{ui("儲存設定")}</button>
              </>)}
          </Modal>)}
    </div>);
}
createRoot(document.getElementById("root")!).render(<React.StrictMode>
    <App />
  </React.StrictMode>);
