import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchIngestConfig, fetchJobs, jobAction, uploadFile, type UploadHandle } from '../lib/api';
import type { IngestConfig, Job, JobAction, RejectedFile } from '../lib/types';
import { INGEST, fill, type Lang } from '../lib/i18n';
import { WarningIcon } from './Icons';
import { JobList } from './JobList';
import { UploadDropzone, type PickedFiles } from './UploadDropzone';
import styles from './IngestTab.module.css';

const PAGE_SIZE = 50;
const POLL_MS = 1500;

interface UploadState {
  index: number;
  total: number;
  name: string;
  pct: number;
}

interface Props {
  lang: Lang;
  onLibraryChanged: () => void;
}

function extOf(name: string): string {
  return name.split('.').pop()?.toLowerCase() ?? '';
}

export function IngestTab({ lang, onLibraryChanged }: Props) {
  const t = INGEST[lang];
  const [config, setConfig] = useState<IngestConfig | null>(null);
  const [recent, setRecent] = useState<Job[]>([]);
  const [older, setOlder] = useState<Job[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [hasActive, setHasActive] = useState(false);
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [rejected, setRejected] = useState<RejectedFile[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const statusById = useRef<Map<string, string>>(new Map());
  const olderLoaded = useRef(false);
  const currentUpload = useRef<UploadHandle | null>(null);
  const uploadCancelled = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const page = await fetchJobs({ limit: PAGE_SIZE });
      let libraryChanged = false;
      for (const job of page.jobs) {
        const before = statusById.current.get(job.id);
        if (job.status === 'added' && before !== undefined && before !== 'added') libraryChanged = true;
        statusById.current.set(job.id, job.status);
      }
      setRecent(page.jobs);
      setHasActive(page.has_active);
      // enquanto nada antigo foi carregado, o cursor segue a primeira pagina
      if (!olderLoaded.current) setNextBefore(page.next_before);
      if (libraryChanged) onLibraryChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [onLibraryChanged]);

  useEffect(() => {
    fetchIngestConfig()
      .then(setConfig)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(refresh);
  }, [refresh]);

  // polling so enquanto houver algo na fila; o processamento em si roda no backend
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [hasActive, refresh]);

  const addJobs = (jobs: Job[]) => {
    for (const job of jobs) statusById.current.set(job.id, job.status);
    setRecent((prev) => [...jobs, ...prev.filter((j) => !jobs.some((n) => n.id === j.id))]);
    if (jobs.some((j) => j.status === 'queued')) setHasActive(true);
  };

  const handlePick = async ({ files, fromFolder }: PickedFiles) => {
    if (!config) return;
    const accepted = new Set(config.formats.flatMap((f) => [f.ext, ...(f.aliases ?? [])]));
    const valid = files.filter((f) => accepted.has(extOf(f.name)));
    const invalid = files.filter((f) => !accepted.has(extOf(f.name)));
    setError(null);
    setNotice(null);
    if (fromFolder) {
      if (invalid.length) setNotice(fill(t.folderIgnored, { n: invalid.length }));
    } else if (invalid.length) {
      setRejected((prev) => [
        ...invalid.map((f) => ({ filename: f.name, reason_code: 'unsupported_format' as const })),
        ...prev,
      ]);
    }
    if (!valid.length) return;

    // um arquivo por requisicao: progresso por arquivo e cancelamento no meio do lote
    uploadCancelled.current = false;
    for (let i = 0; i < valid.length; i++) {
      if (uploadCancelled.current) {
        setNotice(fill(t.uploadCancelled, { n: valid.length - i }));
        break;
      }
      const file = valid[i];
      setUpload({ index: i + 1, total: valid.length, name: file.name, pct: 0 });
      const handle = uploadFile(file, (loaded, total) =>
        setUpload({ index: i + 1, total: valid.length, name: file.name, pct: Math.round((loaded / total) * 100) }),
      );
      currentUpload.current = handle;
      try {
        const result = await handle.promise;
        addJobs(result.jobs);
        if (result.rejected.length) setRejected((prev) => [...result.rejected, ...prev]);
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') {
          setNotice(fill(t.uploadCancelled, { n: valid.length - i }));
          break;
        }
        setError(fill(t.uploadFailed, { error: e instanceof Error ? e.message : String(e) }));
      }
    }
    currentUpload.current = null;
    setUpload(null);
    refresh();
  };

  const cancelUpload = () => {
    uploadCancelled.current = true;
    currentUpload.current?.abort();
  };

  const handleAction = async (id: string, action: JobAction) => {
    setError(null);
    try {
      const job = await jobAction(id, action);
      statusById.current.set(job.id, job.status);
      setRecent((prev) => prev.map((j) => (j.id === job.id ? job : j)));
      setOlder((prev) => prev.filter((j) => j.id !== job.id));
      setHasActive(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      refresh();
    }
  };

  const loadMore = async () => {
    if (!nextBefore) return;
    try {
      const page = await fetchJobs({ limit: PAGE_SIZE, before: nextBefore });
      for (const job of page.jobs) statusById.current.set(job.id, job.status);
      olderLoaded.current = true;
      setOlder((prev) => [...prev, ...page.jobs]);
      setNextBefore(page.next_before);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const recentIds = new Set(recent.map((j) => j.id));
  const jobs = [...recent, ...older.filter((j) => !recentIds.has(j.id))];
  const formatsLabel = config ? config.formats.map((f) => f.label).join(', ') : '';

  return (
    <div className={styles.tab}>
      {config && !config.enabled && (
        <div className={styles.notice} role="status">
          <WarningIcon size={16} />
          <p>{t.disabledNotice}</p>
        </div>
      )}

      {config && (
        <UploadDropzone config={config} disabled={!config.enabled} busy={upload !== null} lang={lang} onPick={handlePick} />
      )}

      {upload && (
        <div className={styles.uploadPanel} role="status">
          <div className={styles.uploadHead}>
            <span className={styles.uploadName}>
              {fill(t.uploadProgress, { i: upload.index, n: upload.total, name: upload.name })}
            </span>
            <span className={`${styles.uploadPct} tabular`}>{upload.pct}%</span>
          </div>
          <div className={styles.track}>
            <div className={styles.bar} style={{ width: `${upload.pct}%` }} />
          </div>
          <button type="button" className={styles.cancelButton} onClick={cancelUpload}>
            {t.cancelUpload}
          </button>
        </div>
      )}

      {notice && <p className={styles.info}>{notice}</p>}

      {error && (
        <div className={styles.error} role="alert">
          <WarningIcon size={16} />
          <p>{error}</p>
        </div>
      )}

      {rejected.length > 0 && (
        <ul className={styles.rejected}>
          {rejected.map((r, i) => (
            <li key={`${r.filename}-${i}`}>
              <span className={styles.rejectedName}>{r.filename}</span>
              <span>{fill(t.reason[r.reason_code], { formats: formatsLabel })}</span>
            </li>
          ))}
        </ul>
      )}

      <JobList
        jobs={jobs}
        lang={lang}
        ocrAvailable={config?.ocr_available ?? false}
        formatsLabel={formatsLabel}
        hasMore={nextBefore !== null}
        onLoadMore={loadMore}
        onAction={handleAction}
      />
    </div>
  );
}
