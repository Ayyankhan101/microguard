// Copy the built SPA into the Python package so `pip install microguard` ships
// a dashboard that works without Node.
import { cp, rm, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, '..', 'dist');
const target = resolve(here, '..', '..', 'microguard', 'dashboard', 'static');

await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
await cp(dist, target, { recursive: true });
console.log(`copied ${dist} -> ${target}`);
