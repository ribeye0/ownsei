import { TranscriptList } from "./TranscriptList";
import { Sidebar } from "./Sidebar";
import { modelLabels } from "./labels";
import { timingLabel } from "./asrLabel";
import { useExecution } from "./ExecutionSettings";
import { Icon } from "./Icon";
import { followCue, followScrollTop } from "./transcriptFollow";
import { useAsrFilter } from "./AsrFilterSettings";
import { ProcessingDialog } from "./ProcessingDialog";
import { t as ui } from "./i18n";
import { translationDefaults } from "./TranslationSettings";
import { Modal } from "./Modal";
import { usePreferences } from "./usePreferences";
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { api, type Project } from "./api";
import { ProjectManager } from "./ProjectManager";
import {
  PROJECT_NUMBER_KEY,
  nextProjectTitle,
  projectNumber,
  nextProjectSelection,
} from "./projectState";
import { ImportDialog } from "./ImportDialog";
import { AudioPlayer } from "./AudioPlayer";
import { JobPanel, type Job } from "./JobPanel";
import { issueReport, resultStamp } from "./resultInfo";
import { RequestLane } from "./requestLane";
import { ExportDialog } from "./ExportDialog";
import { ModelManager, type ManagedModel } from "./ModelManager";
import { BatchDialog } from "./BatchDialog";
import {
  TranslationPreferencesFields,
  type TranslationPreferences,
  type StyleVariants,
  type TargetLanguage,
} from "./TranslationPreferences";
import { GlossaryEditor } from "./GlossaryEditor";
import { GlobalGlossaryDialog } from "./GlobalGlossaryDialog";
import { readEntries, type TermEntry } from "./terminology";
import type {
  Cue,
  Details,
  TranscriptVersion,
  TranslationVersion,
  TrackView,
} from "./types";
import { ResultControls } from "./ResultControls";
import { CueEditor } from "./CueEditor";
import { useJobs } from "./useJobs";
import { liveJob } from "./jobState";
import { editRequest, dirtyDraft } from "./editorContract";
const time = (ms: number) =>
  `${Math.floor(ms / 60000)
    .toString()
    .padStart(2, "0")}:${((ms / 1000) % 60).toFixed(2).padStart(5, "0")}`;
function App() {
  const { language, languagePreference, theme, setLanguage, setTheme } = usePreferences();
  const modelNames = modelLabels();
  const [defaultStyles, setDefaultStyles] = useState<StyleVariants>({
    "zh-Hant": "",
    "zh-Hans": "",
  });
  const [defaultTerms, setDefaultTerms] = useState<TermEntry[]>([]);
  const [modelCatalog, setModelCatalog] = useState<ManagedModel[]>([]);
  const modelRequest = useRef(0);
  async function refreshModels() {
    const request = ++modelRequest.current;
    try {
      const value = await api<{ models: ManagedModel[] }>("/models");
      if (request === modelRequest.current) setModelCatalog(value.models);
    } catch {
      /* Model manager displays fetch errors. */
    }
  }
  useEffect(() => {
    void refreshModels();
    const timer = setInterval(() => void refreshModels(), 5000);
    return () => {
      clearInterval(timer);
      modelRequest.current++;
    };
  }, []);
  const [preferences, setPreferences] = useState<TranslationPreferences>({
    target_language: "zh-Hant",
    translation_style: "",
  });
  const [projects, setProjects] = useState<Project[]>([]),
    [pid, setPid] = useState(localStorage.getItem("onsei:project") || ""),
    [loadedPid, setLoadedPid] = useState(""),
    [details, setDetails] = useState<Details | null>(null),
    [tid, setTid] = useState("");
  const [cues, setCues] = useState<Cue[]>([]),
    [jobs, setJobs] = useState<Job[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [active, setActive] = useState("");
  const [asr, setAsr] = useState("haas-ja-v1.0-f16"),
    [translation, setTranslation] = useState("index-nativelong-9b"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState(""),
    [projectSearch, setProjectSearch] = useState(""),
    [onlyFlags, setOnlyFlags] = useState(false),
    [files, setFiles] = useState<string[]>([]),
    [dialog, setDialog] = useState("");
  const [playerExpanded, setPlayerExpanded] = useState(false);
  const [translationOptions, setTranslationOptions] = useState(
    translationDefaults("index-nativelong-9b"),
  );
  const [asrFilter, setAsrFilter] = useAsrFilter();
  const [execution, setExecution] = useExecution();
  const [processMode, setProcessMode] = useState("run");
  const [workspaceTab, setWorkspaceTab] = useState("subtitles");
  const [trackSearch, setTrackSearch] = useState("");
  const styleReturn = useRef("");
  const [reportFull, setReportFull] = useState("");
  const [reportText, setReportText] = useState(""),
    [reportCopied, setReportCopied] = useState(false);
  const transcriptList = useRef<HTMLElement>(null);
  const [followPlayback, setFollowPlayback] = useState(
    () => localStorage.getItem("ownsei:follow-playback") !== "false",
  );
  const [draft, setDraft] = useState<Cue | null>(null),
    [notes, setNotes] = useState(""),
    [glossary, setGlossary] = useState("{}"),
    [path, setPath] = useState(""),
    [title, setTitle] = useState("");
  const [versions, setVersions] = useState<TranscriptVersion[]>([]),
    [translationVersions, setTranslationVersions] = useState<
      TranslationVersion[]
    >([]),
    [deleteTarget, setDeleteTarget] = useState<{
      pid: string;
      tid: string;
      kind: "versions" | "translations";
      id: string;
      label: string;
    } | null>(null),
    [now, setNow] = useState(0),
    [segmentEnd, setSegmentEnd] = useState<number | null>(null);
  const audio = useRef<HTMLAudioElement>(null),
    searchInput = useRef<HTMLInputElement>(null),
    rowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const base = `/projects/${pid}`;
  const projectAvailable = projects.some((project) => project.id === pid);
  const [resultsLoading, setResultsLoading] = useState(false);
  const projectReads = useRef(new RequestLane());
  const projectListRequest = useRef(0);
  const resultReads = useRef(new RequestLane(setResultsLoading));
  const pendingActions = useRef(0);
  const selectionRef = useRef({ pid, tid });
  selectionRef.current = { pid, tid };
  const target = details?.settings.target_language || "zh-Hant";
  const projectPreferences: TranslationPreferences = {
    target_language: target,
    translation_style:
      details?.settings.translation_style || defaultStyles[target],
    translation_styles: {
      ...defaultStyles,
      ...details?.settings.translation_styles,
    },
  };
  function openStyle() {
    styleReturn.current = dialog === "process" ? "process" : "";
    setPreferences(projectPreferences);
    setError("");
    setDialog("style");
  }
  async function changeTarget(target_language: TargetLanguage) {
    projectReads.current.cancel();
    await api(base + "/settings", "PATCH", { target_language });
    await refresh();
  }
  const track = details?.tracks.find((t) => t.id === tid);
  const hasRunning = jobs.some((j) => liveJob(j.status));
  const activeTranslation = translationVersions.find((v) => v.active);
  const transcriptLabel = (v?: TranscriptVersion) =>
    v
      ? `${modelNames[v.model || ""] || ui("匯入")}${timingLabel(v)}${v.extra_alignment ? ui(" + 對齊") : ""} · ${resultStamp(v.created_at, v.created_at_estimated)}`
      : ui("尚無轉錄");
  async function report(cue?: Cue) {
    if (!track) return;
    setReportCopied(false);
    const context = {
      projectTitle: details?.settings.title || pid,
      trackName: track.name,
      transcriptLabel: transcriptLabel(
        versions.find((v) => v.version === track.active_version),
      ),
      translationLabel: activeTranslation
        ? `${modelNames[activeTranslation.model] || activeTranslation.model} · ${activeTranslation.stage === "review" ? ui("審校稿") : ui("初譯")} · ${resultStamp(activeTranslation.created_at)}`
        : ui("尚無中文結果"),
      location: {
        project: pid,
        track: tid,
        transcript: track.active_version,
        translation: activeTranslation?.id || null,
        cue: cue?.id || null,
      },
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
    } catch {
      /* Full IDs remain available if a concurrent deletion invalidates the short reference. */
    }
  }
  const guard = async (fn: () => Promise<void>) => {
    const owner = { ...selectionRef.current };
    pendingActions.current += 1;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      if (
        selectionRef.current.pid === owner.pid &&
        selectionRef.current.tid === owner.tid
      )
        setError(String(e));
    } finally {
      pendingActions.current -= 1;
      setBusy(pendingActions.current > 0);
    }
  };
  async function refreshProjects(missingId?: string) {
    const request = ++projectListRequest.current;
    const next = await api<Project[]>("/projects");
    if (request !== projectListRequest.current) return;
    setProjects(next);
    const selected = nextProjectSelection(
      projects.map((p) => p.id),
      next.map((p) => p.id),
      selectionRef.current.pid,
    );
    if (selected !== selectionRef.current.pid) {
      if (missingId && missingId === selectionRef.current.pid && originalDraft.current) {
        setError(ui("此專案已不在目前工作區，請先複製需要保留的校對內容，再切換專案。"));
      } else {
        selectProject(selected, true);
      }
    }
    return next;
  }
  async function deleteProject(id: string) {
    await api(`/projects/${id}`, "DELETE");
    await refreshProjects();
  }
  function newProject() {
    setTitle(
      nextProjectTitle(
        projects.map((p) => p.title),
        ui("專案"),
        Number(localStorage.getItem(PROJECT_NUMBER_KEY)),
      ),
    );
    setPath("");
    setError("");
    setDialog("create");
  }
  function selectProject(id: string, discardDeleted = false) {
    if (id === selectionRef.current.pid) return true;
    if (!discardDeleted && !discardDraft()) return false;
    if (discardDeleted) originalDraft.current = null;
    audio.current?.pause();
    projectReads.current.cancel();
    resultReads.current.cancel();
    selectionRef.current = { pid: id, tid: "" };
    setPid(id);
    setError("");
    setWorkspaceTab("subtitles");
    setTrackSearch("");
    setDetails(null);
    setLoadedPid("");
    setTid("");
    setCues([]);
    setJobs([]);
    setFiles([]);
    setVersions([]);
    setTranslationVersions([]);
    setDeleteTarget(null);
    setPlayerExpanded(false);
    setNow(0);
    setFilter("");
    setOnlyFlags(false);
    setActive("");
    setSelected([]);
    setDraft(null);
    setSegmentEnd(null);
    return true;
  }
  async function refresh() {
    if (!pid || selectionRef.current.pid !== pid) return;
    await projectReads.current.run(
      (signal) => api<Details>(base, "GET", undefined, { signal }),
      (d) => {
        if (selectionRef.current.pid !== pid) return;
        setDetails(d);
        setLoadedPid(pid);
        setJobs(d.jobs);
        const current = selectionRef.current.tid;
        if (!current || !d.tracks.some((t) => t.id === current)) {
          const stored = localStorage.getItem(`onsei:track:${pid}`);
          const next =
            d.tracks.find((t) => t.id === stored)?.id || d.tracks[0]?.id || "";
          setTid(next);
        }
      },
    );
  }
  function applyView(view: TrackView) {
    if (
      selectionRef.current.pid !== pid ||
      selectionRef.current.tid !== view.track.id
    )
      return;
    setDetails((current) =>
      current
        ? {
            ...current,
            tracks: current.tracks.map((t) =>
              t.id === view.track.id ? view.track : t,
            ),
          }
        : current,
    );
    setCues(view.cues);
    setVersions(view.versions);
    setTranslationVersions(view.translations);
    setFiles(view.files);
  }
  async function refreshCues() {
    if (selectionRef.current.pid !== pid || selectionRef.current.tid !== tid)
      return;
    if (
      !pid ||
      !tid ||
      loadedPid !== pid ||
      !details?.tracks.some((t) => t.id === tid)
    ) {
      setCues([]);
      return;
    }
    await resultReads.current.run(
      (signal) =>
        api<TrackView>(base + `/tracks/${tid}/view`, "GET", undefined, {
          signal,
        }),
      applyView,
    );
  }
  const refreshRef = useRef({ refresh, refreshCues });
  refreshRef.current = { refresh, refreshCues };
  useEffect(() => {
    refreshProjects().catch((e) => setError(String(e)));
    void api<{
      translation_styles: StyleVariants;
      glossary_entries: TermEntry[];
      asr: string;
      translation: string;
    }>("/preferences/defaults")
      .then((d) => {
        setDefaultStyles(d.translation_styles);
        setAsr(d.asr);
        setTranslation(d.translation);
        setTranslationOptions(translationDefaults(d.translation));
        setDefaultTerms(d.glossary_entries);
      })
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    localStorage.setItem("ownsei:follow-playback", String(followPlayback));
  }, [followPlayback]);
  useEffect(() => {
    setNow(0);
    setSegmentEnd(null);
    setPlayerExpanded(false);
    setCues([]);
    setFiles([]);
  }, [pid, tid]);
  useEffect(() => {
    if (pid && !projectAvailable) return;
    if (pid) localStorage.setItem("onsei:project", pid);
    else localStorage.removeItem("onsei:project");
    setTid("");
    setActive("");
    setSelected([]);
    setDraft(null);
    refresh().catch((e) => {
      if (selectionRef.current.pid === pid) setError(String(e));
    });
    return () => projectReads.current.cancel();
  }, [pid, projectAvailable]);
  useEffect(() => {
    setActive("");
    setSelected([]);
    setDraft(null);
    setVersions([]);
    setTranslationVersions([]);
    refreshCues().catch((e) => {
      if (selectionRef.current.pid === pid && selectionRef.current.tid === tid)
        setError(String(e));
    });
    return () => resultReads.current.cancel();
  }, [pid, tid, loadedPid]);
  useJobs(base, projectAvailable, {
    onProjectUnavailable: async () => {
      if (selectionRef.current.pid !== pid) return false;
      const next = await refreshProjects(pid);
      return selectionRef.current.pid === pid && (!next || next.some((p) => p.id === pid));
    },
    onJobs: setJobs,
    onError: (e) => setError(String(e)),
    onResultsChanged: async () => {
      if (pendingActions.current) return false;
      await refreshRef.current.refresh();
      if (pendingActions.current) return false;
      await refreshRef.current.refreshCues();
      return true;
    },
  });
  function switchResult(kind: "versions" | "translations", id: string) {
    if (!discardDraft()) return;
    guard(async () => {
      projectReads.current.cancel();
      resultReads.current.cancel();
      const view = await api<TrackView>(
        base + `/tracks/${tid}/${kind}/${id}`,
        "POST",
      );
      if (selectionRef.current.pid !== pid || selectionRef.current.tid !== tid)
        return;
      if (kind === "translations") {
        const model = translationVersions.find((v) => v.id === id)?.model;
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
    if (
      id === tid ||
      !details?.tracks.some((t) => t.id === id) ||
      !discardDraft()
    )
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
      fresh_asr: ["run", "transcribe"].includes(action),
      asr,
      translation,
      track_ids: all ? null : [tid],
      cue_ids: local ? selected : null,
      translation_options: translationOptions,
      asr_filter: {
        ...asrFilter,
        enabled: asrFilter.enabled,
      },
      execution,
    });
    await refresh();
  }
  const originalDraft = useRef<Cue | null>(null);
  const draftDirty = dirtyDraft(originalDraft.current, draft);
  function discardDraft() {
    if (
      draftDirty &&
      !window.confirm(ui("目前有未儲存的校對，確定放棄這些修改嗎？"))
    )
      return false;
    originalDraft.current = null;
    setDraft(null);
    return true;
  }
  function choose(c: Cue) {
    if (draft?.id === c.id) return;
    if (!discardDraft()) return;
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
  }
  async function save() {
    if (!draft) return;
    const original = originalDraft.current;
    if (!original) return;
    await api(
      base + `/cues/${draft.id}`,
      "PATCH",
      editRequest(original, draft),
    );
    await Promise.all([refreshCues(), refresh()]);
    originalDraft.current = null;
    setDraft(null);
  }
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      const modalOpen =
        !!dialog || !!deleteTarget || !!reportText || playerExpanded;
      if (e.ctrlKey && e.key === "Enter" && draft && !modalOpen) {
        e.preventDefault();
        guard(save);
      }
      const typing =
        e.target instanceof Element &&
        e.target.closest("input,textarea,select,[contenteditable=true]");
      if (e.key === "/" && !typing && !modalOpen) {
        e.preventDefault();
        setWorkspaceTab("subtitles");
        requestAnimationFrame(() => searchInput.current?.focus());
      }
      if (e.key === "Escape" && reportText) {
        setReportText("");
        return;
      }
      if (e.key === "Escape" && dialog !== "import" && !playerExpanded) {
        if (discardDraft()) {
          setDialog("");
          setDeleteTarget(null);
          setActive("");
        }
      }
      if (
        e.code === "Space" &&
        !modalOpen &&
        !typing &&
        !(e.target instanceof Element && e.target.closest("button,a")) &&
        audio.current
      ) {
        e.preventDefault();
        if (audio.current.paused)
          audio.current.play().catch((e) => setError(String(e)));
        else audio.current.pause();
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [draft, cues, dialog, deleteTarget, reportText, playerExpanded]);
  useEffect(() => {
    if (!draftDirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [draftDirty]);
  const visible = cues.filter(
    (c) =>
      (!onlyFlags ||
        c.review_flags.length > 0 ||
        c.timing_status === "stale" ||
        c.timing_status === "alignment_failed" ||
        c.translation_status === "stale" ||
        c.translation_status === "pending" ||
        c.translation_status === "failed") &&
      (!filter || c.ja.includes(filter) || c.zh?.includes(filter)),
  );
  useEffect(() => {
    if (
      !followPlayback ||
      draft ||
      workspaceTab !== "subtitles" ||
      playerExpanded ||
      dialog ||
      deleteTarget ||
      reportText
    )
      return;
    const id = followCue(cues, now);
    const row = id && rowRefs.current[id],
      list = transcriptList.current;
    if (!row || !list) return;
    const rowRect = row.getBoundingClientRect(),
      listRect = list.getBoundingClientRect();
    const top = followScrollTop(
      list.scrollTop,
      list.clientHeight,
      rowRect.top - listRect.top - list.clientTop,
      rowRect.height,
    );
    if (top !== undefined) list.scrollTo({ top });
  }, [
    now,
    cues,
    followPlayback,
    draft,
    workspaceTab,
    playerExpanded,
    dialog,
    deleteTarget,
    reportText,
    filter,
    onlyFlags,
  ]);
  return (
    <div className={"app" + (track ? " has-player" : "")}>
      <Sidebar
        projects={projects}
        pid={pid}
        theme={theme}
        projectSearch={projectSearch}
        busy={busy}
        inert={playerExpanded || !!dialog || !!deleteTarget || !!reportText}
        onNew={newProject}
        onSelect={selectProject}
        onDelete={(id) => void guard(() => deleteProject(id))}
        onSearch={setProjectSearch}
        onDialog={setDialog}
      />
      <main
        inert={playerExpanded || !!dialog || !!deleteTarget || !!reportText}
      >
        <div className="project-content">
          <header>
            <div>
              <h1>{details?.settings.title || ui("日文轉錄與中文字幕")}</h1>
            </div>
            {pid && (
              <div className="header-actions">
                <button
                  onClick={() => {
                    setDialog("settings");
                    setNotes(details?.settings.notes || "");
                    setGlossary(
                      JSON.stringify(
                        details?.settings.glossary_entries ||
                          details?.settings.glossary ||
                          {},
                        null,
                        2,
                      ),
                    );
                  }}
                >
                  <Icon name="settings" />
                  {ui("專案設定")}
                </button>
                <button
                  onClick={() => {
                    setDialog("import");
                    setPath("");
                  }}
                >
                  <Icon name="plus" />
                  {ui("匯入音檔")}
                </button>
                <button
                  disabled={busy || !tid}
                  onClick={() => {
                    if (discardDraft()) setDialog("batch");
                  }}
                >
                  <Icon name="layers" />
                  {ui("批量處理")}
                </button>
                <button
                  disabled={
                    busy || !details?.tracks.some((t) => t.active_version)
                  }
                  onClick={() => {
                    if (discardDraft()) setDialog("export");
                  }}
                >
                  <Icon name="export" />
                  {ui("匯出字幕")}
                </button>
              </div>
            )}
          </header>
          {error && (
            <div className="error" role="alert">
              {error}
              <button className="icon" onClick={() => setError("")}>
                ×
              </button>
            </div>
          )}
          {!pid ? (
            <div className="welcome">
              <div className="welcome-icon">
                <Icon name="wave" />
              </div>
              <h2>
                {projects.length
                  ? ui("開啟專案，繼續工作")
                  : ui("開始第一個字幕專案")}
              </h2>
              <p>
                {ui("從本機音檔開始。轉錄、翻譯與人工校對各自保存，")}
                <br />
                {ui("隨時可以接著上次的進度。")}
              </p>
              <div className="welcome-actions">
                {projects.length > 0 && (
                  <button
                    className="primary"
                    onClick={() => setDialog("manage")}
                  >
                    <Icon name="folder" />
                    {ui("開啟專案")}
                  </button>
                )}
                <button
                  className={projects.length ? "" : "primary"}
                  onClick={newProject}
                >
                  <Icon name="plus" />
                  {projects.length ? ui("新增專案") : ui("建立第一個專案")}
                </button>
              </div>
              <small>{ui("專案資料保存在本機，可隨時繼續處理")}</small>
            </div>
          ) : (
            <>
              <div className="project-workspace">
                <section className="track-panel" aria-label={ui("音軌清單")}>
                  <div className="track-panel-heading">
                    <h2>{ui("音軌")}</h2>
                    <span>{details?.tracks.length || 0}</span>
                  </div>
                  <input
                    aria-label={ui("搜尋音軌")}
                    placeholder={ui("搜尋音軌…")}
                    value={trackSearch}
                    onChange={(e) => setTrackSearch(e.target.value)}
                  />
                  <div className="track-list">
                    {details?.tracks
                      .filter((t) =>
                        t.name
                          .toLowerCase()
                          .includes(trackSearch.toLowerCase()),
                      )
                      .map((t) => (
                        <button
                          key={t.id}
                          className={
                            "track-item " + (t.id === tid ? "current" : "")
                          }
                          aria-current={t.id === tid ? "true" : undefined}
                          disabled={busy}
                          onClick={() => selectTrack(t.id)}
                          title={t.name}
                        >
                          <Icon name="audio" />
                          <span>
                            <strong>{t.name}</strong>
                            <small>
                              {time(t.duration_ms)} ·{" "}
                              {t.active_version
                                ? ui("已有逐字稿")
                                : ui("未轉錄")}
                            </small>
                          </span>
                        </button>
                      ))}
                  </div>
                  {!details?.tracks.length && (
                    <p className="panel-empty">
                      {ui("匯入音檔後會顯示在這裡")}
                    </p>
                  )}
                  {!!details?.tracks.length &&
                    !details.tracks.some((t) =>
                      t.name.toLowerCase().includes(trackSearch.toLowerCase()),
                    ) && <p className="panel-empty">{ui("沒有符合的音軌")}</p>}
                  <button
                    className="track-import"
                    onClick={() => setDialog("import")}
                  >
                    <Icon name="plus" />
                    {ui("加入音檔")}
                  </button>
                </section>
                <section className="work-area">
                  <div className="track-heading">
                    <div>
                      <h2 title={track?.name}>
                        {track?.name || ui("尚未匯入音檔")}
                      </h2>
                    </div>
                    <button
                      className="primary"
                      disabled={busy || resultsLoading || !tid}
                      onClick={() => {
                        if (discardDraft()) {
                          setProcessMode(cues.length ? "translate" : "run");
                          setError("");
                          setDialog("process");
                        }
                      }}
                    >
                      <Icon name="play" />
                      {ui("處理音軌")}
                    </button>
                  </div>
                  <div
                    className="workspace-tabs"
                    onKeyDown={(e) => {
                      if (
                        ["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                          e.key,
                        )
                      ) {
                        e.preventDefault();
                        const next =
                          e.key === "Home"
                            ? "subtitles"
                            : e.key === "End"
                              ? "jobs"
                              : workspaceTab === "subtitles"
                                ? "jobs"
                                : "subtitles";
                        setWorkspaceTab(next);
                        document.getElementById(next + "-tab")?.focus();
                      }
                    }}
                    role="tablist"
                    aria-label={ui("音軌工作區")}
                  >
                    <button
                      id="subtitles-tab"
                      role="tab"
                      tabIndex={workspaceTab === "subtitles" ? 0 : -1}
                      aria-selected={workspaceTab === "subtitles"}
                      aria-controls="subtitles-panel"
                      onClick={() => setWorkspaceTab("subtitles")}
                    >
                      <Icon name="list" />
                      {ui("字幕校訂")}
                      <span>{cues.length}</span>
                    </button>
                    <button
                      id="jobs-tab"
                      role="tab"
                      tabIndex={workspaceTab === "jobs" ? 0 : -1}
                      aria-selected={workspaceTab === "jobs"}
                      aria-controls="jobs-panel"
                      onClick={() => setWorkspaceTab("jobs")}
                    >
                      <Icon name="layers" />
                      {ui("任務紀錄")}
                      {hasRunning && <span className="activity-dot" />}
                    </button>
                  </div>
                  {hasRunning && workspaceTab !== "jobs" && (
                    <button
                      className="task-notice"
                      onClick={() => setWorkspaceTab("jobs")}
                    >
                      <span className="activity-dot" />
                      {ui("處理進行中")}
                      {" · "}
                      {jobs.find((j) => liveJob(j.status))?.message}
                      <Icon name="chevron" />
                    </button>
                  )}
                  <div
                    id="jobs-panel"
                    role="tabpanel"
                    aria-labelledby="jobs-tab"
                    hidden={workspaceTab !== "jobs"}
                  >
                    <JobPanel
                      base={base}
                      jobs={jobs}
                      busy={busy}
                      onAction={(id, action) =>
                        guard(async () => {
                          await api(base + `/jobs/${id}/${action}`, "POST");
                          await refresh();
                        })
                      }
                    />
                  </div>
                  <div
                    id="subtitles-panel"
                    role="tabpanel"
                    aria-labelledby="subtitles-tab"
                    hidden={workspaceTab !== "subtitles"}
                  >
                    <ResultControls
                      track={track}
                      versions={versions}
                      translations={translationVersions}
                      busy={busy}
                      running={hasRunning}
                      loading={resultsLoading}
                      models={modelNames}
                      label={transcriptLabel}
                      time={time}
                      onSwitch={switchResult}
                      onDelete={(kind, id, label) => {
                        if (discardDraft())
                          setDeleteTarget({ pid, tid, kind, id, label });
                      }}
                      onReload={() =>
                        guard(async () => {
                          await refresh();
                          await refreshCues();
                        })
                      }
                      onReport={() => report()}
                      filesCount={files.length}
                      onFiles={() => setDialog("files")}
                    />
                    <section className="editor-tools">
                      <div className="tabs">
                        <b>{ui("雙語逐字稿")}</b>
                        <span>
                          {visible.length} / {cues.length}
                          {ui(" 句")}
                        </span>
                        <span>
                          {ui("疑點 ")}
                          {cues.filter((c) => c.review_flags.length > 0).length}
                        </span>
                      </div>
                      <div className="search-tools">
                        <button
                          className="follow-playback"
                          aria-pressed={followPlayback}
                          disabled={!cues.length}
                          title={ui("隨播放與時間跳轉定位台詞；校訂時暫停跟隨")}
                          onClick={() => setFollowPlayback(!followPlayback)}
                        >
                          <Icon name="play" />
                          {ui("跟隨播放")}
                        </button>
                        <input
                          ref={searchInput}
                          aria-label={ui("搜尋台詞")}
                          placeholder={ui("搜尋日文或中文…")}
                          value={filter}
                          onChange={(e) => setFilter(e.target.value)}
                        />
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={onlyFlags}
                            onChange={(e) => setOnlyFlags(e.target.checked)}
                          />
                          {ui("只看疑點／待更新")}
                        </label>
                      </div>
                    </section>
                    {selected.length > 0 && (
                      <div className="selection">
                        <span>
                          {ui("已選 ")}
                          {selected.length}
                          {ui(" 句")}
                        </span>
                        <button
                          onClick={() =>
                            guard(() => start("translate", false, true))
                          }
                        >
                          {ui("重譯相關批次")}
                        </button>
                        <button onClick={() => setSelected([])}>
                          {ui("清除選取")}
                        </button>
                      </div>
                    )}
                    <div className="table-head">
                      <span>{ui("時間 / 選取")}</span>
                      <span>{ui("日文原文")}</span>
                      <span>
                        {ui("中文譯文")}
                        {activeTranslation?.target_language === "zh-Hans"
                          ? ui("（簡體）")
                          : activeTranslation?.target_language === "zh-Hant"
                            ? ui("（繁體）")
                            : ""}
                      </span>
                      <span>{ui("狀態")}</span>
                    </div>
                    {draft && !cues.some((c) => c.id === draft.id) && (
                      <div className="error" role="alert">
                        {ui(
                          "結果已在其他操作中切換，未保存草稿仍保留；請複製後重新開啟需要校對的結果。",
                        )}
                        <textarea
                          aria-label={ui("保留的日文草稿")}
                          readOnly
                          value={draft.ja}
                        />
                        <textarea
                          aria-label={ui("保留的中文草稿")}
                          readOnly
                          value={draft.zh || ""}
                        />
                        <button onClick={() => discardDraft()}>
                          {ui("關閉草稿")}
                        </button>
                      </div>
                    )}
                    <TranscriptList
                      cues={visible}
                      total={cues.length}
                      hasTrack={!!track}
                      active={active}
                      now={now}
                      selected={selected}
                      onSelect={setSelected}
                      onPlay={play}
                      onEdit={choose}
                      onReport={(c) => void report(c)}
                      time={time}
                      listRef={transcriptList}
                      rowRefs={rowRefs}
                      renderEditor={(c) => (
                        <>
                          {active === c.id && draft?.id === c.id && (
                            <CueEditor
                              cue={c}
                              draft={draft}
                              busy={busy}
                              changed={
                                originalDraft.current?.revision !== c.revision
                              }
                              onChange={setDraft}
                              onSave={() => guard(save)}
                              onClose={() => {
                                if (discardDraft()) setActive("");
                              }}
                              onReset={() =>
                                guard(async () => {
                                  if (
                                    draftDirty &&
                                    !window.confirm(
                                      ui("確定放棄草稿並恢復模型文字嗎？"),
                                    )
                                  )
                                    return;
                                  const original = originalDraft.current;
                                  if (!original) return;
                                  await api(
                                    base + `/cues/${original.id}`,
                                    "PATCH",
                                    {
                                      revision: original.revision,
                                      translation_version:
                                        original.provenance
                                          .translation_version ?? null,
                                      changes: {
                                        ja_override: null,
                                        zh_override: null,
                                      },
                                    },
                                  );
                                  originalDraft.current = null;
                                  setDraft(null);
                                  await refreshCues();
                                })
                              }
                            />
                          )}
                        </>
                      )}
                    />
                  </div>
                </section>
              </div>
            </>
          )}
        </div>
      </main>
      {track && (
        <div inert={!!dialog || !!deleteTarget || !!reportText}>
          <AudioPlayer
            key={`${pid}:${tid}`}
            track={track}
            source={"/api" + base + `/tracks/${tid}/audio`}
            projectTitle={details?.settings.title || ""}
            versionLabel={`${modelNames[versions.find((v) => v.version === track.active_version)?.model || ""] || ui("日文")} / ${activeTranslation ? modelNames[activeTranslation.model] || activeTranslation.model : ui("尚無中文譯文")}`}
            cues={cues}
            audioRef={audio}
            now={now}
            onTime={setNow}
            followPlayback={followPlayback}
            onFollowPlayback={setFollowPlayback}
            segmentEnd={segmentEnd}
            onSegmentEnd={setSegmentEnd}
            tracks={details?.tracks || []}
            onTrack={selectTrack}
            switchingDisabled={busy}
            expanded={playerExpanded}
            onExpanded={setPlayerExpanded}
          />
        </div>
      )}
      {reportText && (
        <div className="modal-backdrop">
          <section
            className="modal issue-report"
            role="dialog"
            aria-modal="true"
            aria-label={ui("問題定位資訊")}
          >
            <h2>{ui("回報問題")}</h2>
            <p>
              {ui(
                "複製這份資訊並附在問題回報中，再補充實際遇到的問題。已包含專案、音軌、目前日文／中文結果與句段定位；不會自動傳送。",
              )}
            </p>
            <textarea
              aria-label={ui("問題定位內容")}
              readOnly
              value={reportText}
              onFocus={(e) => e.currentTarget.select()}
            />
            <div className="manager-actions">
              <button onClick={() => setReportText("")}>{ui("關閉")}</button>
              <button
                onClick={() =>
                  void navigator.clipboard
                    .writeText(reportFull)
                    .then(() => setReportCopied(true))
                    .catch(() => setReportCopied(false))
                }
              >
                {ui("複製完整診斷")}
              </button>
              <button
                onClick={() =>
                  void navigator.clipboard
                    .writeText(reportText)
                    .then(() => setReportCopied(true))
                    .catch(() => setReportCopied(false))
                }
              >
                {ui("複製定位資訊")}
              </button>
            </div>
            <small role="status">
              {reportCopied
                ? ui("已複製，可直接貼上回報")
                : ui("若無法複製，可點選上方內容後按 Ctrl+C")}
            </small>
          </section>
        </div>
      )}
      {deleteTarget && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={ui("刪除結果確認")}
          >
            <h2>
              {ui("刪除 ")}
              {deleteTarget.label}{" "}
              {deleteTarget.kind === "versions" ? ui("轉錄") : ui("譯文")}？
            </h2>
            <p>
              {deleteTarget.kind === "versions"
                ? ui(
                    "會移除此轉錄及其所有模型的中文結果，包含這些版本的人工校對。",
                  )
                : ui(
                    "會移除這份中文結果及其中的人工中文校對；其他初譯與審校稿保留，包含由這份結果建立的審校稿。",
                  )}
            </p>
            <p>
              {ui(
                "原始音檔保留。刪除資料會備份到專案資料夾的 trash；下次處理會重新推論。",
              )}
            </p>
            <div className="manager-actions">
              <button disabled={busy} onClick={() => setDeleteTarget(null)}>
                {ui("取消")}
              </button>
              <button
                className="danger"
                disabled={busy || hasRunning}
                onClick={() =>
                  guard(async () => {
                    await api(
                      `/projects/${deleteTarget.pid}/tracks/${deleteTarget.tid}/${deleteTarget.kind}/${deleteTarget.id}`,
                      "DELETE",
                    );
                    setDeleteTarget(null);
                    setDraft(null);
                    setActive("");
                    setSelected([]);
                    await refresh();
                    await refreshCues();
                  })
                }
              >
                {ui("確認刪除")}
              </button>
            </div>
          </section>
        </div>
      )}
      {dialog === "process" ? (
        <ProcessingDialog
          mode={processMode}
          onMode={setProcessMode}
          track={track?.name || ""}
          hasTranscript={!!cues.length}
          asr={asr}
          translation={translation}
          options={translationOptions}
          execution={execution}
          onExecution={setExecution}
          asrFilter={asrFilter}
          onAsrFilter={setAsrFilter}
          catalog={modelCatalog}
          target={target}
          busy={busy}
          error={error}
          onAsr={setAsr}
          onTranslation={setTranslation}
          onOptions={setTranslationOptions}
          onTarget={(v) => guard(() => changeTarget(v))}
          onStyle={openStyle}
          onStart={(mode) =>
            guard(async () => {
              await start(mode);
              setDialog("");
              setWorkspaceTab("jobs");
            })
          }
          onClose={() => setDialog("")}
        />
      ) : dialog === "files" ? (
        <Modal title={ui("既有匯出檔案")} onClose={() => setDialog("")}>
          <div className="file-list">
            {files.map((f) => (
              <a
                key={f}
                href={"/api" + base + "/exports/" + encodeURIComponent(f)}
                download
              >
                {f}
              </a>
            ))}
          </div>
        </Modal>
      ) : dialog === "shortcuts" ? (
        <Modal
          title={ui("快捷鍵說明")}
          onClose={() => setDialog("")}
          className="shortcuts-dialog"
        >
          <dl className="shortcuts-list">
            <div>
              <dt>
                <kbd>Space</kbd>
              </dt>
              <dd>{ui("播放／暫停")}</dd>
            </div>
            <div>
              <dt>
                <kbd>/</kbd>
              </dt>
              <dd>{ui("搜尋台詞")}</dd>
            </div>
            <div>
              <dt>
                <kbd>Ctrl Enter</kbd>
              </dt>
              <dd>{ui("儲存校對")}</dd>
            </div>
            <div>
              <dt>
                <kbd>Esc</kbd>
              </dt>
              <dd>{ui("收起／關閉")}</dd>
            </div>
          </dl>
        </Modal>
      ) : dialog === "preferences" ? (
        <Modal title={ui("介面設定")} onClose={() => setDialog("")}>
          <label>
            {ui("介面語言")}
            <select
              value={languagePreference}
              onChange={(e) =>
                setLanguage(e.target.value as "auto" | "zh-Hant" | "zh-Hans")
              }
            >
              <option value="auto">{ui("跟隨系統")}</option>
              <option value="zh-Hant">繁體中文</option>
              <option value="zh-Hans">简体中文</option>
            </select>
          </label>
          <small>{ui("未手動選擇時，依瀏覽器提供的系統語言決定繁簡體；非中文環境使用繁體中文。")}</small>
          <label>
            {ui("配色主題")}
            <select value={theme} onChange={(e) => setTheme(e.target.value)}>
              <option value="classic">{ui("淺色")}</option>
              <option value="mocha">{ui("深色")}</option>
            </select>
          </label>
        </Modal>
      ) : dialog === "global" ? (
        <GlobalGlossaryDialog
          defaults={defaultTerms}
          onClose={() => setDialog("")}
        />
      ) : dialog === "style" ? (
        <Modal
          title={ui("翻譯風格指引")}
          onClose={() => setDialog(styleReturn.current)}
          busy={busy}
          className="style-dialog"
        >
          <p>
            {ui("正在編輯：")}
            {preferences.target_language === "zh-Hans"
              ? ui("简体中文")
              : ui("繁體中文")}
          </p>
          <TranslationPreferencesFields
            value={preferences}
            onChange={setPreferences}
            defaultStyles={defaultStyles}
            disabled={busy}
            showTarget={false}
          />
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="manager-actions">
            <button
              disabled={busy}
              onClick={() => setDialog(styleReturn.current)}
            >
              {ui("取消")}
            </button>
            <button
              className="primary"
              disabled={busy || !preferences.translation_style.trim()}
              onClick={() =>
                guard(async () => {
                  await api(base + "/settings", "PATCH", {
                    translation_styles: {
                      [preferences.target_language]:
                        preferences.translation_style,
                    },
                  });
                  await refresh();
                  setDialog(styleReturn.current);
                })
              }
            >
              {ui("儲存風格指引")}
            </button>
          </div>
        </Modal>
      ) : dialog === "manage" ? (
        <ProjectManager
          onClose={() => setDialog("")}
          onOpen={(id) => {
            if (selectProject(id)) setDialog("");
          }}
          onChanged={async () => {
            await refreshProjects();
            if (selectionRef.current.pid === pid) await refresh();
          }}
        />
      ) : dialog === "models" ? (
        <ModelManager
          onClose={() => setDialog("")}
          onChanged={() => void refreshModels()}
        />
      ) : dialog === "export" ? (
        <ExportDialog
          key={base}
          base={base}
          tracks={details?.tracks || []}
          current={tid}
          models={modelNames}
          onClose={() => setDialog("")}
        />
      ) : dialog === "batch" ? (
        <BatchDialog
          key={base}
          base={base}
          tracks={details?.tracks || []}
          current={tid}
          asr={asr}
          translation={translation}
          translationOptions={translationOptions}
          execution={execution}
          onExecution={setExecution}
          asrFilter={asrFilter}
          onAsrFilter={setAsrFilter}
          catalog={modelCatalog}
          models={modelNames}
          defaultStyles={defaultStyles}
          preferences={projectPreferences}
          onClose={() => setDialog("")}
          onSubmitted={async () => {
            await refresh();
            setWorkspaceTab("jobs");
          }}
        />
      ) : dialog === "import" ? (
        <ImportDialog
          key={base}
          base={base}
          onClose={() => setDialog("")}
          onChanged={async () => {
            await refresh();
            await refreshProjects();
          }}
        />
      ) : (
        dialog && (
          <Modal
            title={dialog === "create" ? ui("建立專案") : ui("專案設定")}
            onClose={() => setDialog("")}
            busy={busy}
          >
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            {dialog === "create" ? (
              <>
                <label>
                  {ui("專案標題")}
                  <input
                    autoFocus
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder={ui("專案名稱")}
                  />
                </label>
                <details className="project-copy">
                  <summary>{ui("從既有專案複製（選填）")}</summary>
                  <label>
                    {ui("既有專案目錄")}
                    <input
                      value={path}
                      onChange={(e) => setPath(e.target.value)}
                      placeholder={ui("填寫專案資料夾的完整路徑")}
                    />
                  </label>
                  <small>{ui("複製逐字稿與譯文，保留原專案及原始音檔")}</small>
                </details>
                <button
                  className="primary"
                  disabled={busy || !title.trim()}
                  onClick={() =>
                    guard(async () => {
                      if (!discardDraft()) return;
                      const p = await api<{
                        id: string;
                      }>("/projects", "POST", {
                        title,
                        copy_from: path || null,
                        target_language: language,
                      });
                      localStorage.setItem(
                        PROJECT_NUMBER_KEY,
                        String(
                          Math.max(
                            projectNumber(title),
                            Number(localStorage.getItem(PROJECT_NUMBER_KEY)) ||
                              0,
                          ),
                        ),
                      );
                      await refreshProjects();
                      selectProject(p.id);
                      setDialog("import");
                    })
                  }
                >
                  {ui("建立並匯入音檔")}
                </button>
              </>
            ) : (
              <>
                <label>
                  {ui("角色、稱謂與場景備註")}
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder={ui(
                      "例如：說話者：學妹。聽者：學長。旁白：無。學長沒有配音；學妹直接向學長問話、調侃。只填已知設定。",
                    )}
                  />
                </label>
                <GlossaryEditor
                  value={glossary}
                  onChange={setGlossary}
                  scope="project"
                  disabled={busy}
                />
                <small>
                  {ui(
                    "設定用於此專案的翻譯。專案術語優先於全局術語；修改後會標記機器譯文待更新，人工譯文保留。",
                  )}
                </small>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    guard(async () => {
                      await api(base + "/settings", "PATCH", {
                        notes,
                        glossary_entries: readEntries(glossary),
                      });
                      await refresh();
                      await refreshCues();
                      setDialog("");
                    })
                  }
                >
                  {ui("儲存設定")}
                </button>
              </>
            )}
          </Modal>
        )
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
