// Hub reports network traffic and reconstructed file bytes independently.
// Compression, cached data and retries can change how much crosses the network.
export function modelDownload(job: {
  transfer_bytes_done?: number;
  bytes_done: number;
  bytes_total: number;
  files_total: number;
}, sourceEstimate: number) {
  const network = job.transfer_bytes_done !== undefined;
  const received = Math.max(0, job.transfer_bytes_done ?? job.bytes_done);
  // With several files the Hub discovers their sizes one by one. An early
  // metadata file alone is a poor estimate for the whole download.
  const total = network && (job.files_total > 1 || !job.bytes_total)
    ? Math.max(job.bytes_total, sourceEstimate)
    : job.bytes_total;
  // A transfer can exceed even the complete source size (e.g. Xet chunks).
  // Keep showing measured bytes instead of pinning an invalid estimate at 100%.
  const percent = total > 0 && (network ? received < total : received <= total)
    ? received / total * 100
    : undefined;
  return {network, received, percent};
}
