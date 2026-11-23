import {useEffect, useRef} from "react";
import {api, ApiError} from "./api";
import type {Job} from "./JobPanel";
import {finishedJobs} from "./jobState";

/** One finite, non-overlapping poll per project, independent of selected track. */
export function useJobs(base:string, enabled:boolean, callbacks:{
  onJobs:(jobs:Job[])=>void; onError:(error:unknown)=>void;
  onProjectUnavailable:()=>Promise<boolean>;
  onResultsChanged:()=>Promise<boolean>;
}) {
  const current = useRef(callbacks);
  current.current = callbacks;
  useEffect(()=>{
    if (!enabled) return;
    const controller = new AbortController();
    let timer:ReturnType<typeof setTimeout>;
    let previous:string|null = null;
    let recheckProject = false;
    const poll = async () => {
      try {
        if (!recheckProject) {
          const jobs = await api<Job[]>(base+"/jobs", "GET", undefined, {signal:controller.signal, timeoutMs:10000})
            .catch(error => {
              if (error instanceof ApiError && (error.status === 404 || error.status === 410)) return null;
              throw error;
            });
          if (controller.signal.aborted) return;
          if (jobs) {
            current.current.onJobs(jobs);
            const done = finishedJobs(jobs);
            if (previous===null || done===previous || await current.current.onResultsChanged()) previous=done;
          } else {
            recheckProject = true;
          }
        }
        if (recheckProject && !controller.signal.aborted) {
          // Retry a failed catalog read before sending this project another request.
          if (!await current.current.onProjectUnavailable()) return;
          recheckProject = false;
        }
      } catch(error) {
        if (!controller.signal.aborted) current.current.onError(error);
      }
      if (!controller.signal.aborted) timer=setTimeout(poll,2000);
    };
    void poll();
    return ()=>{controller.abort(); clearTimeout(timer);};
  },[base,enabled]);
}
