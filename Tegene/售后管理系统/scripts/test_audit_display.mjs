import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from '../frontend/node_modules/typescript/lib/typescript.js'
const source = readFileSync(new URL('../frontend/src/utils/auditDisplay.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText
const {changes,summary,moduleLabel,actionLabel,resourceLabel} = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
const row={module:'field',action:'save',resource_type:'save_person',before_data:JSON.stringify({name:'小李',phone:'13800000000'}),after_data:JSON.stringify({name:'小李',phone:'13900000000',password:'secret'})}
assert.equal(moduleLabel(row),'人员花名册')
assert.equal(actionLabel(row),'保存')
assert.equal(resourceLabel(row),'人员资料')
assert.deepEqual(changes(row),[{field:'手机号',before:'13800000000',after:'13900000000'}])
assert.equal(summary(row),'手机号')
assert.equal(changes({...row,before_data:null,after_data:'{"name":"旧记录"}'})[0].before,'未记录')
assert.equal(summary({...row,before_data:null,after_data:'broken json'}),'未记录具体修改项')
assert.equal(moduleLabel({module:'field',resource_type:'configure_project'}),'项目库')
assert.equal(actionLabel({action:'transfer-member'}),'调动人员')
const layout=readFileSync(new URL('../frontend/src/views/LayoutView.vue',import.meta.url),'utf8')
assert.ok(!layout.includes("label:'事件监控'"))
console.log('Audit display checks passed')
