import { useState } from 'react';
import type { Job, JobAction } from '../lib/types';
import { INGEST, fill, type Lang } from '../lib/i18n';
import { formatBytes, formatDate } from '../lib/format';
import { CheckIcon } from './Icons';
import styles from './JobList.module.css';

const ACTIVE = new Set(['queued', 'processing']);

interface Props {
  jobs: Job[];
  lang: Lang;
  ocrAvailable: boolean;
  formatsLabel: string;
  hasMore: boolean;
  onLoadMore: () => void;
  onAction: (id: string, action: JobAction) => void;
}

function reasonText(job: Job, lang: Lang, formatsLabel: string): string | null {
  if (!job.reason_code) return null;
  return fill(INGEST[lang].reason[job.reason_code] ?? job.reason_code, {
    duplicate_of: job.duplicate_of ?? '—',
    n: job.pages_without_text,
    total: job.pages_total,
    formats: formatsLabel,
  });
}

function JobRow({ job, lang, ocrAvailable, formatsLabel, onAction }: Omit<Props, 'jobs' | 'hasMore' | 'onLoadMore'> & { job: Job }) {
  const t = INGEST[lang];
  const [confirmingOcr, setConfirmingOcr] = useState(false);
  const active = ACTIVE.has(job.status);
  const reason = reasonText(job, lang, formatsLabel);
  const pct = job.progress && job.progress.total ? Math.round((job.progress.current / job.progress.total) * 100) : null;
  // uma acao nova do backend que esta versao da pagina nao conhece nao vira botao vazio
  const actions = job.actions.filter((a) => t.action[a]);

  const trigger = (action: JobAction) => {
    if (action === 'ocr' && !confirmingOcr) {
      setConfirmingOcr(true);
      return;
    }
    setConfirmingOcr(false);
    onAction(job.id, action);
  };

  return (
    <li className={`${styles.row}${active ? ` ${styles.rowActive}` : ''}`}>
      <div className={styles.rowHead}>
        <span className={styles.filename} title={job.filename}>
          {job.filename}
        </span>
        <span className={`${styles.badge} ${styles[`badge_${job.status}`]}`}>
          {job.status === 'added' && <CheckIcon size={12} />}
          {t.status[job.status]}
        </span>
      </div>

      <div className={styles.meta}>
        {job.format.toUpperCase()} · {formatBytes(job.size_bytes)} · {formatDate(job.created_at, lang)}
      </div>

      {job.status === 'processing' && (
        <div className={styles.progressBlock}>
          <span className={styles.stage}>
            {job.cancel_requested ? t.cancelling : job.stage ? t.stage[job.stage] : t.status.processing}
            {job.progress && (
              <span className="tabular">
                {' '}
                {job.progress.current}/{job.progress.total}
              </span>
            )}
          </span>
          <div className={styles.track} role="progressbar" aria-valuenow={pct ?? undefined} aria-valuemin={0} aria-valuemax={100}>
            <div
              className={`${styles.bar}${pct === null ? ` ${styles.barIndeterminate}` : ''}`}
              style={pct === null ? undefined : { width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {job.result && (
        <div className={styles.result}>
          <span className={styles.bookTitle}>{job.result.book_title}</span>
          <span className={styles.resultMeta}>
            {job.result.lang.toUpperCase()} · <span className="tabular">{job.result.n_chunks}</span> {t.chunksUnit}
            {job.result.pages_without_text && job.pages_total
              ? ` · ${fill(t.pagesWithoutText, { n: job.result.pages_without_text, total: job.pages_total })}`
              : ''}
          </span>
        </div>
      )}

      {reason && <p className={styles.reason}>{reason}</p>}
      {job.status === 'no_text' && !ocrAvailable && <p className={styles.note}>{t.ocrUnavailable}</p>}

      {confirmingOcr ? (
        <div className={styles.confirm}>
          <p>{t.ocrConfirm}</p>
          <div className={styles.actions}>
            <button type="button" className={styles.actionButton} onClick={() => trigger('ocr')}>
              {t.ocrConfirmStart}
            </button>
            <button type="button" className={styles.linkButton} onClick={() => setConfirmingOcr(false)}>
              {t.cancel}
            </button>
          </div>
        </div>
      ) : (
        actions.length > 0 && (
          <div className={styles.actions}>
            {actions.map((action) => (
              <button key={action} type="button" className={styles.actionButton} onClick={() => trigger(action)}>
                {t.action[action]}
              </button>
            ))}
          </div>
        )
      )}
    </li>
  );
}

export function JobList({ jobs, hasMore, onLoadMore, ...rowProps }: Props) {
  const t = INGEST[rowProps.lang];
  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const history = jobs.filter((j) => !ACTIVE.has(j.status));

  return (
    <div className={styles.wrap}>
      {active.length > 0 && (
        <section>
          <h2 className={styles.heading}>{t.activeHeading}</h2>
          <ul className={styles.list}>
            {active.map((job) => (
              <JobRow key={job.id} job={job} {...rowProps} />
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className={styles.heading}>{t.historyHeading}</h2>
        {history.length === 0 ? (
          <p className={styles.empty}>{t.emptyHistory}</p>
        ) : (
          <ul className={styles.list}>
            {history.map((job) => (
              <JobRow key={job.id} job={job} {...rowProps} />
            ))}
          </ul>
        )}
        {hasMore && (
          <button type="button" className={styles.loadMore} onClick={onLoadMore}>
            {t.loadMore}
          </button>
        )}
      </section>
    </div>
  );
}
