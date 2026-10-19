export const KIND_ORDER = ['paper','survey','model','dataset','benchmark','agent','framework','infrastructure','tool','product','company'];
export const FEATURED = ['OSWorld','UI-TARS','Browser Use','OpenCUA','AgentNet','AndroidWorld','WebArena','UI-Venus','Stagehand','ScreenSpot Pro','Agent S','Holo Model Family'];
export const PAGE_SIZE = 24;
export const PLATFORM_GROUPS = {desktop:['desktop','windows','macos','linux'],mobile:['android','ios','harmonyos']};
export const KINDS = {paper:['Papers','论文','▤'],survey:['Surveys','综述','≋'],model:['Models','模型','◈'],dataset:['Datasets','数据集','▦'],benchmark:['Benchmarks','评测基准','◎'],agent:['Agents','智能体','↗'],framework:['Frameworks','框架','◇'],infrastructure:['Infrastructure','基础设施','▧'],tool:['Tools','工具','⚒'],product:['Products','产品','□'],company:['Publishers','发布方','⌂']};
export const PLATFORMS = {web:['Web','浏览器'],desktop:['Desktop','桌面'],windows:['Windows','Windows'],macos:['macOS','macOS'],linux:['Linux','Linux'],mobile:['Mobile','移动设备'],android:['Android','Android'],ios:['iOS','iOS'],harmonyos:['HarmonyOS','鸿蒙'],'cross-platform':['Cross-platform','跨平台'],unspecified:['Unassigned','待标注']};
export function createRecordLoader(fetcher) {
  const cache=new Map();
  return async function loadRecord(record) {
    if(!cache.has(record.id))cache.set(record.id,fetcher(record.page_url+'record.json').then(response=>{if(!response.ok)throw new Error('HTTP '+response.status);return response.json();}).then(row=>{if(row.id!==record.id||!['curated','metadata_verified'].includes(row.review_status))throw new Error('Resource snapshot mismatch');return row;}).catch(error=>{cache.delete(record.id);throw error;}));
    return cache.get(record.id);
  };
}
export function normalize(value) { return String(value || '').normalize('NFKC').toLowerCase().replace(/[\p{P}\p{S}]+/gu,' ').replace(/\s+/g,' ').trim(); }
// Skip LaTeX command names and tokens without letters or digits: \textsc{GUI-Spotlight} gives GS and "DSBC : Data" gives DD.
export function initials(name) { return String(name || '').replace(/\\[a-zA-Z]+/g,'').split(/[\s-]+/).map(word => word.match(/[\p{L}\p{N}]/u)?.[0]).filter(Boolean).slice(0,2).join('').toUpperCase(); }
export function bibliographicDate(record) {
  if(typeof record.paper_date==='string')return record.paper_date;
  const publication=record.details?.publication;
  if(publication&&!record.id?.startsWith('arxiv:')){
    const parts=publication.date_parts;
    return Array.isArray(parts)&&parts.length>=1&&parts.length<=3&&parts.every(Number.isInteger)
      ? parts.map((part,index)=>String(part).padStart(index?2:4,'0')).join('-') : '';
  }
  return record.published_at || '';
}
export function compactFacts(record, slotLabel = slot => slot) {
  const year=bibliographicDate(record).slice(0,4), arxiv=record.paper_arxiv;
  return [year&&{key:'year',text:year},arxiv&&{key:'paper',text:'arXiv '+arxiv,url:'https://arxiv.org/abs/'+arxiv},...Object.entries(record.declared_licenses || {}).map(([slot,name])=>({key:'license',text:slotLabel(slot)+' '+name}))].filter(Boolean);
}
export const TOPIC_ALIASES = {'reinforcement-learning':['RL'],'human-in-the-loop':['HITL']};
const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
export function searchText(values) {
  const text = values.filter(Boolean).join(' ');
  // Index the parts of compound names too, so word-start search finds "osworld" in macOSWorld and "llm" in SpreadsheetLLM.
  const parts = text.split(/\s+/).filter(word => /\p{Ll}\p{Lu}/u.test(word)).map(word => word.replace(/(\p{Ll})(\p{Lu})/gu,'$1 $2'));
  return normalize([text,...parts].join(' '));
}
// Index the English and Chinese labels of a record's kind, platforms and topics rather than their keys; topic labels come from data/topics.json through index.json.
export function recordSearchText(record, topics = {}) {
  return searchText([record.id,record.url,record.name,record.summary,record.publication_search_text,...record.aliases,...record.authors,...record.environments.flatMap(x=>PLATFORMS[x] || [x]),...record.topics.flatMap(x=>[...(topics[x] || [x]),...(TOPIC_ALIASES[x] || [])]),...(KINDS[record.kind] || [])]);
}
export function profileVisible(text, environments, terms, environment) { return (!environment || environments.includes(environment)) && terms.every(term => text.includes(term)); }
export function matches(record, state, ignoreKind = false) {
  if (!ignoreKind && state.kind && record.kind !== state.kind) return false;
  if (state.platform && !(PLATFORM_GROUPS[state.platform] || [state.platform]).some(x => record.environments.includes(x))) return false;
  if (state.review && record.review_status !== state.review) return false;
  if (state.topic && !record.topics.includes(state.topic)) return false;
  if (state.year && bibliographicDate(record).slice(0,4) !== state.year) return false;
  const terms = normalize(state.q).split(' ').filter(Boolean);
  // Latin and digit terms match at word starts; CJK text is not space-delimited, so it keeps substring matching.
  if (terms.every(term => wordStart(record.search_text,term))) return true;
  // A compound name typed with spaces ("os world", "ui tars") still finds OSWorld and UI-TARS.
  return terms.length > 1 && compactHit(nameText(record).names,terms.join(''));
}
const wordStart = (text, term) => CJK.test(term) ? text.includes(term) : text.startsWith(term) || text.includes(' '+term);
// True when consecutive words of a name, joined without spaces, start with the compact query; "uitars" is in "ARPO UI-TARS" but not in "GUITAR: Structured…".
function compactHit(names, compact) {
  for (const words of names) for (let i = 0; i < words.length; i++) {
    let joined = '';
    for (let j = i; j < words.length && joined.length < compact.length; j++) joined += words[j];
    if (joined.startsWith(compact)) return true;
  }
  return false;
}
// Names and reader-facing aliases (not IDs or URLs), normalized once per record for matching and ranking.
const NAME_TEXT = new WeakMap();
function nameText(record) {
  let text = NAME_TEXT.get(record);
  if (!text) {
    const names = [record.name,...(record.aliases || []).filter(alias => !/[:/]/.test(alias))].map(normalize);
    text = {names: names.map(name => name.split(' ').filter(Boolean)), exact: names, words: searchText([record.name,...names.slice(1)])};
    NAME_TEXT.set(record, text);
  }
  return text;
}
// 0: the name or an alias is the query; 1: the name starts with it; 2: every term is in the name or an alias; 3: matched elsewhere (summary, authors, tags).
export function nameRelevance(record, q) {
  const terms = q.split(' ').filter(Boolean), compact = terms.join(''), text = nameText(record);
  if (!terms.length) return 3;
  if (text.exact.some(name => name === q || name.replaceAll(' ','') === compact)) return 0;
  if (!CJK.test(q) && (text.exact[0].startsWith(q) || text.exact[0].replaceAll(' ','').startsWith(compact))) return 1;
  return terms.every(term => wordStart(text.words,term)) || (terms.length > 1 && compactHit(text.names,compact)) ? 2 : 3;
}
const collator = new Intl.Collator('en',{sensitivity:'base'});
const FEATURED_RANK = new Map(FEATURED.map((name,i) => [name,i]));
const alpha = (a,b) => collator.compare(a.name,b.name);
export function filtered(records, state) {
  const result = records.filter(r => matches(r,state));
  // Bibliographic dates are ISO prefixes, so code-unit order is chronological; compute each once per sort.
  const dates = new Map(result.map(r => [r,bibliographicDate(r)]));
  const newer = (a,b) => {const x=dates.get(a), y=dates.get(b); return x<y ? 1 : x>y ? -1 : 0;};
  if (state.sort === 'name') result.sort(alpha);
  else if (state.sort === 'newest') result.sort((a,b) => newer(a,b) || alpha(a,b));
  else {
    const papers = ['paper','survey'].includes(state.kind), q = normalize(state.q);
    // With a query, records named after it come before records that only mention it in a summary or tag.
    const relevance = new Map(q ? result.map(r => [r,nameRelevance(r,q)]) : []), tier = r => relevance.get(r) ?? 3;
    const rank = r => r.kind !== 'company' && FEATURED_RANK.has(r.name) ? FEATURED_RANK.get(r.name) : FEATURED.length;
    result.sort((a,b) => Number(a.review_status !== 'curated') - Number(b.review_status !== 'curated') || tier(a)-tier(b) || (papers ? newer(a,b) || alpha(a,b) : rank(a)-rank(b) || alpha(a,b)));
  }
  return result;
}
export function parseState(search) {
  const p = new URLSearchParams(search);
  const kind=KIND_ORDER.includes(p.get('kind')) ? p.get('kind') : '';
  return {q:p.get('q') || '',kind,platform:p.get('platform') || '',review:['curated','metadata_verified'].includes(p.get('review')) ? p.get('review') : '',topic:p.get('topic') || '',year:/^(19|20)\d{2}$/.test(p.get('year') || '') ? p.get('year') : '',sort:['name','newest'].includes(p.get('sort')) ? p.get('sort') : 'featured',page:Math.max(1,parseInt(p.get('page'),10)||1),view:['list','grid'].includes(p.get('view')) ? p.get('view') : ['paper','survey'].includes(kind) ? 'list' : 'grid'};
}
export function serializeState(state, lang, record='') {
  const p = new URLSearchParams();
  for (const key of ['q','kind','platform','review','topic','year']) if (state[key]) p.set(key,state[key]);
  if (state.sort !== 'featured') p.set('sort',state.sort);
  if (state.page > 1) p.set('page',String(state.page));
  if (state.view !== (['paper','survey'].includes(state.kind) ? 'list' : 'grid')) p.set('view',state.view);
  if (lang === 'zh') p.set('lang','zh');
  if (record) p.set('record',record);
  return p.toString();
}
// First, last and neighbouring pages; null marks a gap, and a gap of one page shows that page instead.
export function pageWindow(page, pages) {
  const shown = [...new Set([1,page-1,page,page+1,pages])].filter(n => n>=1 && n<=pages).sort((a,b) => a-b);
  return shown.flatMap((n,i) => !i || n-shown[i-1]===1 ? [n] : n-shown[i-1]===2 ? [n-1,n] : [null,n]);
}
export function escapeHTML(value) { return String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
export function safeURL(value) { try {const u=new URL(value); return ['https:','http:'].includes(u.protocol) ? u.href : '#';} catch {return '#';} }
export const FACT_LABELS = {sample_count:['Sample count','样本数'],sample_unit:['Sample unit','样本单位'],screen_count:['Screen count','界面数量'],example_count:['Example count','示例数量'],modalities:['Modalities','数据模态'],languages:['Languages','语言'],gated:['Access gated','访问需申请'],disabled:['Disabled on Hub','Hub 已停用'],hub_id:['Hub identifier','Hub 标识'],download_url:['Download location','下载入口'],size_category_reported:['Publisher size category','发布方规模分类'],reported_scale:['Reported scale','发布方描述规模'],artifact_type:['Artifact type','资产类型'],task_count:['Task count','任务数量'],task_unit:['Task unit','任务单位'],metrics:['Metrics','评测指标'],evaluation_type:['Evaluation method','评测方式'],access:['Access','访问方式'],pricing_url:['Pricing','价格页面'],availability:['Availability','可用性'],leaderboard_url:['Leaderboard','排行榜'],dataset_profile:['Dataset profile','数据集概况'],benchmark_protocol:['Evaluation protocol','评测协议'],platform_constraints:['Platform constraints','平台限制'],artifact_inspection:['Artifact inspection','资产检查范围'],base_model:['Base model','基础模型'],hub_revision:['Hub revision','Hub 修订版本'],listed_weight_files:['Listed weight files','列出的权重文件'],license_name:['License name','许可名称'],dataverse:['Dataverse release','Dataverse 发布版本'],release_tag:['Release tag','发布标签'],repository_revision:['Repository revision','仓库修订版本'],repository_id:['GitHub repository ID','GitHub 仓库 ID'],repository_tree_sha:['Git tree SHA','Git 目录树 SHA'],repository_url:['Implementation repository','代码仓库'],release_revision:['Release revision','发布修订版本'],dataset_release:['Dataset release','数据集发布版本'],evaluation_caution:['Evaluation caution','评测注意事项'],license_components_note:['License components','许可组成说明'],maintenance_status:['Maintenance status','维护状态'],component_path:['Component path','组件路径'],license_conflict:['License conflict','许可声明冲突'],package_version:['Package version','软件包版本'],architecture:['Architecture','架构'],parameter_label:['Parameter size label','参数规模标注'],training_data_note:['Training data note','训练数据说明']};
const PROFILE_PARTS = {dataset_profile:['supervision','modalities','formats','splits','counts'],benchmark_protocol:['focus','setting','observations','actions','evaluation','counts']};
const PROFILE_LABELS = {supervision:['Supervision','监督信号'],modalities:['Modalities','数据模态'],formats:['Formats','文件格式'],splits:['Splits','数据划分'],focus:['Focus','评测重点'],setting:['Setting','评测设置'],observations:['Observations','观测输入'],actions:['Actions / outputs','动作 / 输出'],evaluation:['Evaluation','评测方法'],counts:['Scale','规模']};
const PREFIXES = {exact:'',approximately:'≈ ','at-least':'≥ ','more-than':'> ','>':'> ','>=':'≥ '};
export function formatDate(value, lang='en') { return new Date(value.length === 10 ? value+'T00:00:00Z' : value).toLocaleDateString(lang === 'zh' ? 'zh-CN' : 'en-US',{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}); }
export function formatFact(key, value, lang='en') {
  const zh=lang==='zh', num=n=>Number(n).toLocaleString(zh?'zh-CN':'en-US');
  // Nested objects are summarized or dropped; String() would render them as "[object Object]".
  const text=v=>v===null||v===undefined?'':typeof v==='boolean'?(zh?(v?'是':'否'):(v?'Yes':'No')):typeof v==='number'?num(v):Array.isArray(v)?v.map(text).filter(Boolean).join(' · '):typeof v==='object'?(typeof v.name==='string'?v.name:Object.entries(v).map(([k,x])=>[k,text(x)]).filter(([,x])=>x).map(([k,x])=>k.replaceAll('_',' ')+': '+x).join(' · ')):/^\d{4}-\d\d-\d\d(T[\d:.]+Z)?$/.test(v)?formatDate(v,lang):String(v);
  const count=c=>{const q=c.qualifier ?? c.comparison;return (PREFIXES[q] ?? (q?q+' ':''))+num(c.value)+' '+c.unit;};
  if(key==='repository_id'&&Number.isSafeInteger(value)&&value>0) return String(value);
  if(key==='gated'&&(value==='auto'||value==='manual')) return zh?(value==='auto'?'是（自动批准）':'是（人工审批）'):(value==='auto'?'Yes, approved automatically':'Yes, approved manually');
  if(!value||typeof value!=='object'||Array.isArray(value)) return text(value);
  if(key==='reported_scale') return count(value);
  if(key==='dataverse') return [value.publisher,value.version_number!=null&&`v${value.version_number}.${value.version_minor_number ?? 0}`,value.file_count!=null&&num(value.file_count)+(zh?' 个文件':' files'),value.license?.name,value.publication_date&&(zh?'发布于 ':'published ')+formatDate(value.publication_date,lang)].filter(Boolean).join(' · ');
  if(PROFILE_PARTS[key]) return PROFILE_PARTS[key].map(part=>[part,part==='counts'?(value.counts || []).map(c=>`${count(c)} (${c.population}${c.basis==='computed'?(zh?'，计算值':', computed'):''})`).join('; '):Array.isArray(value[part])?value[part].join(', '):text(value[part])]).filter(([,x])=>x).map(([part,x])=>PROFILE_LABELS[part][zh?1:0]+(zh?'：':': ')+x).join('\n');
  return text(value);
}
