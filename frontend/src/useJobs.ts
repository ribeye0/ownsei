import {useEffect, useRef} from "react";
import {api} from "./api";
import type {Job} from "./JobPanel";
import {finishedJobs} from "./jobState";

/** One finite, non-overlapping poll per project, independent of selected track. */
export function useJobs(base:string, enabled:boolean, callbacks:{
  onJobs:(jobs:Job[])=>void; onError:(error:unknown)=>void;
  onResultsChanged:()=>Promise<boolean>;
}) {
  const current = useRef(callbacks);
  current.current = callbacks;
  useEffect(()=>{
    if (!enabled) return;
    const controller = new AbortController();
    let timer:ReturnType<typeof setTimeout>;
    let previous:string|null = null;
    const poll = async () => {
      try {
        const jobs = await api<Job[]>(base+"/jobs", "GET", undefined, {signal:controller.signal, timeoutMs:10000});
        if (controller.signal.aborted) return;
        current.current.onJobs(jobs);
        const done = finishedJobs(jobs);
        if (previous===null || done===previous || await current.current.onResultsChanged()) previous=done;
      } catch(error) {if (!controller.signal.aborted) current.current.onError(error);}
      finally {if (!controller.signal.aborted) timer=setTimeout(poll,2000);}
    };
    void poll();
    return ()=>{controller.abort(); clearTimeout(timer);};
  },[base,enabled]);
}
