export const liveJob = (status: string) => ["queued", "waiting", "running", "cancelling"].includes(status);
export const finishedJobs = (jobs: {id:string; status:string}[]) => jobs.filter(j=>!liveJob(j.status)).map(j=>j.id+j.status).join(",");
