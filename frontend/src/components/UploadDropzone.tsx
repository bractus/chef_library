import { useEffect, useRef, useState, type DragEvent } from 'react';
import type { IngestConfig } from '../lib/types';
import { INGEST, fill, type Lang } from '../lib/i18n';
import { UploadIcon } from './Icons';
import styles from './UploadDropzone.module.css';

export interface PickedFiles {
  files: File[];
  fromFolder: boolean;
}

interface Props {
  config: IngestConfig;
  disabled: boolean;
  busy: boolean;
  lang: Lang;
  onPick: (picked: PickedFiles) => void;
}

function isHidden(file: File): boolean {
  const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
  return path.split('/').some((part) => part.startsWith('.'));
}

function readEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

function entryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function walk(entry: FileSystemEntry, out: File[]): Promise<void> {
  if (entry.name.startsWith('.')) return;
  if (entry.isFile) {
    out.push(await entryFile(entry as FileSystemFileEntry));
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    // readEntries devolve em lotes (~100 no Chrome): repete ate vir vazio
    for (let batch = await readEntries(reader); batch.length; batch = await readEntries(reader)) {
      for (const child of batch) await walk(child, out);
    }
  }
}

export function UploadDropzone({ config, disabled, busy, lang, onPick }: Props) {
  const t = INGEST[lang];
  const filesRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const inactive = disabled || busy;
  const accept = config.formats.flatMap((f) => [f.ext, ...(f.aliases ?? [])]).map((e) => `.${e}`).join(',');

  // webkitdirectory nao e um atributo tipado no React
  useEffect(() => {
    folderRef.current?.setAttribute('webkitdirectory', '');
  }, []);

  const pick = (files: File[], fromFolder: boolean) => {
    if (inactive || !files.length) return;
    onPick({ files: files.filter((f) => !isHidden(f)), fromFolder });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (inactive) return;
    // as entradas precisam ser lidas ainda dentro do evento; depois o DataTransfer expira
    const entries = Array.from(e.dataTransfer.items)
      .map((item) => item.webkitGetAsEntry?.())
      .filter((entry): entry is FileSystemEntry => !!entry);
    const plainFiles = Array.from(e.dataTransfer.files);
    if (!entries.some((entry) => entry.isDirectory)) {
      pick(plainFiles, false);
      return;
    }
    const out: File[] = [];
    Promise.all(entries.map((entry) => walk(entry, out))).then(() => pick(out, true));
  };

  const onDragOver = (e: DragEvent) => {
    e.preventDefault();
    if (!inactive) setDragging(true);
  };

  return (
    <div
      className={`${styles.zone}${dragging ? ` ${styles.zoneDragging}` : ''}${inactive ? ` ${styles.zoneInactive}` : ''}`}
      onDragOver={onDragOver}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <UploadIcon size={26} className={styles.icon} />
      <p className={styles.title}>{t.dropTitle}</p>
      <p className={styles.hint}>{t.dropHint}</p>
      <div className={styles.buttons}>
        <button type="button" className={styles.chooseButton} disabled={inactive} onClick={() => filesRef.current?.click()}>
          {busy ? t.uploading : t.chooseFiles}
        </button>
        <button type="button" className={styles.folderButton} disabled={inactive} onClick={() => folderRef.current?.click()}>
          {t.chooseFolder}
        </button>
      </div>
      <input
        ref={filesRef}
        className={styles.hiddenInput}
        type="file"
        multiple
        accept={accept}
        tabIndex={-1}
        onChange={(e) => {
          pick(Array.from(e.target.files ?? []), false);
          e.target.value = '';
        }}
      />
      <input
        ref={folderRef}
        className={styles.hiddenInput}
        type="file"
        multiple
        tabIndex={-1}
        onChange={(e) => {
          pick(Array.from(e.target.files ?? []), true);
          e.target.value = '';
        }}
      />
      <p className={styles.formats}>
        {fill(t.acceptedFormats, { formats: config.formats.map((f) => f.label).join(' · ') })}
      </p>
    </div>
  );
}
