import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync('apps/api/static/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)?.[1];
if (!script) throw new Error('Inline page script not found');

class Element {
  constructor(id = '') {
    this.id = id;
    this.hidden = false;
    this.textContent = '';
    this.className = '';
    this.style = {};
    this.value = '';
    this.src = '';
    this.disabled = false;
    this.listeners = new Map();
    this.children = [];
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  appendChild(node) {
    this.children.push(node);
    return node;
  }

  set innerHTML(value) {
    this._innerHTML = value;
    this.children = [];
  }

  get innerHTML() {
    return this._innerHTML || '';
  }
}

const ids = [
  'drop', 'file', 'analyze', 'error', 'preview-orig-wrap', 'preview',
  'preview-img', 'preview-video', 'health-dot', 'camera_id', 'result',
  'status-spinner', 'status-bar', 'status-text', 'vlm-pending',
  'vlm-degraded', 'poll-elapsed', 'alert-badge', 'meta', 'result-img',
  'result-title', 'single-vlm', 'single-security', 'summary', 'vlm-note',
  'observations', 'risks', 'action', 'video-timing-text', 'video-timing',
  'window-results', 'video-debug', 'batch-results', 'batch-meta', 'batch-list',
];
const elements = new Map(ids.map(id => [id, new Element(id)]));
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  },
  querySelector() {
    return { value: 'async' };
  },
  createElement() {
    return new Element();
  },
  createTextNode(value) {
    return { textContent: value };
  },
};

const requests = [];
const context = {
  document,
  URL: { createObjectURL: () => 'blob:test' },
  console,
  FormData,
  setInterval: () => 1,
  clearInterval: () => {},
  fetch: async (url, options = {}) => {
    requests.push({ url, options });
    if (url === '/health') return { json: async () => ({ vlm_queue_depth: 0 }) };
    if (url === '/async/analyze/videos') {
      return {
        ok: true,
        json: async () => ({
          batch_id: 'batch-1',
          items: [
            { filename: 'cam-a.mp4', camera_id: 'cam-a', analysis_id: 'analysis-a' },
            { filename: 'cam-b.mp4', camera_id: 'cam-b', analysis_id: 'analysis-b' },
          ],
        }),
      };
    }
    throw new Error(`Unexpected request: ${url}`);
  },
};
vm.createContext(context);
vm.runInContext(script, context);

context.setFiles([
  { name: 'cam-a.mp4', type: 'video/mp4' },
  { name: 'cam-b.mp4', type: 'video/mp4' },
]);
await elements.get('analyze').listeners.get('click')();

const request = requests.find(request => request.url === '/async/analyze/videos');
console.log(JSON.stringify({
  endpoint: request?.url,
  field_names: request ? [...request.options.body.keys()] : [],
  row_count: elements.get('batch-list').children.length,
  detail_title: elements.get('result-title').textContent,
}));
