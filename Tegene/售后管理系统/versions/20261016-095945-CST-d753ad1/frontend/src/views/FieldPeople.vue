<template>
  <div class="people-page">
    <header class="people-header">
      <div><h1>人员花名册<span>{{ ordinaryPeople.length }}</span></h1></div>
      <div class="header-actions">
        <el-button :icon="Download" @click="downloadTemplate">下载模板</el-button>
        <el-button :icon="Upload" @click="openImport">批量导入</el-button>
        <el-button type="primary" :icon="Plus" @click="edit()">手工录入</el-button>
      </div>
    </header>
    <el-radio-group v-model="rosterGroup" class="roster-groups" aria-label="名册分类">
      <el-radio-button value="employee">普通员工</el-radio-button>
      <el-radio-button value="management">管理员账号</el-radio-button>
    </el-radio-group>
    <section class="people-controls" aria-label="人员筛选">
      <div class="status-filters">
        <button v-for="s in ['',...statuses]" :key="s" type="button" :class="{selected:statusFilter===s}" :aria-pressed="statusFilter===s" @click="statusFilter=s">{{ s?statusLabel(s):'全部人员' }}<span>{{ s?rosterPeople.filter(p=>p.status===s).length:rosterPeople.length }}</span></button>
      </div>
      <div class="search-actions"><el-input v-model="query" :prefix-icon="Search" aria-label="搜索人员" clearable /><el-popover placement="bottom-end" :width="560" trigger="click">
        <template #reference><el-button :icon="Filter" :type="activeFilterCount?'primary':'default'">筛选{{ activeFilterCount?` (${activeFilterCount})`:'' }}</el-button></template>
        <div class="filter-header"><strong>筛选</strong><el-button link type="primary" @click="columnFilters={}">重置</el-button></div>
        <el-form label-position="top" class="column-filters">
          <el-form-item v-for="column in filterColumns" :key="column.key" :label="column.label">
            <el-select v-model="columnFilters[column.key]" multiple filterable clearable collapse-tags collapse-tags-tooltip placeholder="" :aria-label="'筛选'+column.label">
              <el-option v-for="value in filterOptions(column.key)" :key="value" :label="value||'空白'" :value="value" />
            </el-select>
          </el-form-item>
        </el-form>
      </el-popover><el-button :icon="Refresh" circle aria-label="刷新人员" @click="load" /></div>
    </section>
    <div v-loading="loading" class="people-content">
      <div class="people-list-scroll">
        <table class="people-list" :class="{'management-list':rosterGroup==='management'}" aria-label="人员花名册">
          <thead><tr><th scope="col">姓名</th><th v-if="rosterGroup==='management'" scope="col">系统权限</th><th scope="col">公司在职状态</th><th scope="col">联系电话</th><th scope="col">所属部门 / 公司</th><th scope="col">当前所在项目</th><th scope="col">账号状态</th><th scope="col" class="row-action">操作</th></tr></thead>
          <tbody>
            <tr v-for="person in pagedPeople" :key="person.id" :class="{departed:person.status==='离职'}">
              <td><strong class="person-name">{{ person.name }}</strong></td>
              <td v-if="rosterGroup==='management'">{{ roles[person.role] || person.role }}</td>
              <td><span class="employment-status" :class="statusClass(person.status)">{{ statusLabel(person.status) }}</span></td>
              <td class="phone-cell">{{ person.phone }}</td>
              <td><span class="row-primary">{{ person.department_name || '—' }}</span><span class="row-secondary">{{ person.company || '—' }}</span></td>
              <td><span class="project-name" :class="{unassigned:!person.current_project}">{{ person.current_project || '暂未安排' }}</span></td>
              <td><span class="account-state" :class="{locked:!person.is_active}"><el-icon><Lock v-if="!person.is_active" /><CircleCheck v-else /></el-icon>{{ person.status==='离职'?'已锁定':person.is_active?'已开通':'已停用' }}</span></td>
              <td class="row-action"><el-button link type="primary" :aria-label="'编辑'+person.name+'的资料'" @click="edit(person)">编辑<el-icon><ArrowRight /></el-icon></el-button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <el-empty v-if="!loading&&!filtered.length" description="暂无人员" :image-size="100"><el-button v-if="!query&&!statusFilter" type="primary" @click="edit()">手工录入</el-button></el-empty>
      <div class="list-footer"><span>共 {{ filtered.length }} 人<span v-if="filtered.length"> · 当前 {{ (page-1)*pageSize+1 }}–{{ Math.min(page*pageSize,filtered.length) }} 人</span></span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20,50,100]" :total="filtered.length" layout="sizes, prev, pager, next" /></div>
    </div>
    <el-drawer v-model="visible" :title="form.id?'编辑人员资料':'手工录入人员'" size="min(740px, 100vw)" :close-on-click-modal="false" :before-close="closeEditor">
      <el-form label-position="top" class="person-form" @submit.prevent="save">
        <section v-for="group in formGroups" :key="group.title" class="form-section">
          <h3>{{ group.title }}</h3>
          <div class="roster-fields">
            <el-form-item v-for="column in group.columns" :key="column.key" :label="column.label" :required="['name','phone','status'].includes(column.key)">
              <el-select v-if="column.key==='position'" v-model="selectedPosition" clearable filterable placeholder=""><el-option v-for="option in positionOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select>
              <el-select placeholder="" v-else-if="column.kind==='status'" v-model="form.status" @change="statusChanged"><el-option v-for="s in statuses" :key="s" :label="statusLabel(s)" :value="s" /></el-select>
              <el-input v-else-if="column.kind==='date'" v-model="form[column.key]" type="date" />
              <el-input-number v-else-if="column.kind==='number'" v-model="form[column.key]" :min="0" :max="column.key==='age'?150:60000" :precision="0" controls-position="right" />
              <el-input v-else v-model="form[column.key]" :disabled="column.key==='current_project'&&form.project_managed" />
            </el-form-item>
          </div>
        </section>
        <section class="account-settings"><h3>账号设置</h3>
        <el-form-item label="工号"><el-input v-model="form.employee_no" /></el-form-item>
        <el-form-item label="直属主管"><el-select placeholder="" v-model="form.direct_manager_id" clearable filterable><el-option v-for="p in people.filter(x=>x.id!==form.id&&x.is_active)" :key="p.id" :value="p.id" :label="p.name" /></el-select></el-form-item>
        <el-form-item label="系统权限"><el-select placeholder="" v-model="form.role"><el-option v-for="(label,key) in permissionOptions" :key="key" :value="key" :label="label" /></el-select></el-form-item>
        <el-form-item :label="form.id?'重设密码':'初始密码'"><el-input v-model="form.password" type="password" show-password autocomplete="new-password" /></el-form-item>
        <el-form-item label="企微成员账号（userid）"><el-input v-model="form.wecom_userid" /></el-form-item>
        <el-form-item v-if="form.id" label="启用账号"><el-switch v-model="form.is_active" :disabled="form.status==='离职'" /></el-form-item>
        </section>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
      </el-form>
      <template #footer><div class="editor-footer"><div><el-button :disabled="saving" @click="visible=false">取消</el-button><el-button :loading="saving" type="primary" @click="save">{{ form.id?'保存修改':form.status==='离职'?'保存离职资料':'保存并开通账号' }}</el-button></div></div></template>
    </el-drawer>
    <el-dialog v-model="importVisible" title="导入员工花名册" width="min(1000px, 94vw)" :close-on-click-modal="false" :show-close="!importBusy" :close-on-press-escape="!importBusy">
      <el-steps :active="importDone?2:report?1:0" finish-status="success" simple><el-step title="选择花名册" /><el-step title="核对预览" /><el-step title="导入完成" /></el-steps>
      <div class="field-toolbar">
        <el-button @click="downloadTemplate">下载导入模板</el-button>
        <label class="file-picker">选择文件<input type="file" accept=".xlsx,.xls,.csv" aria-label="选择花名册文件" :disabled="importBusy" @change="selectFile" /></label>
        <span>{{ importFile?.name }}</span>
        <el-button :disabled="!importFile || importDone" :loading="importBusy" @click="previewImport">生成预览</el-button>
      </div>
      <el-alert v-if="importError" :title="importError" type="error" :closable="false" />
      <template v-if="report">
        <el-alert class="import-summary" :title="`${importDone?'已导入':'可导入'} ${report.success} 人，${report.failed} 行需处理`" :type="report.failed?'warning':'success'" :closable="false" />
        <div class="import-preview"><details v-for="row in report.rows" :key="row.row" class="import-person"><summary><span class="import-line">第 {{ row.row }} 行</span><strong>{{ row.name || '未填写姓名' }}</strong><span>{{ row.phone }}</span><span class="employment-status" :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span><span :class="{'import-error':row.error}">{{ row.error || row.action }}</span></summary><p v-if="row.error" class="import-error">{{ row.error }}</p><dl class="preview-fields"><div v-for="column in columns" :key="column.key"><dt>{{ column.label }}</dt><dd>{{ row[column.key]??'—' }}</dd></div></dl></details></div>
      </template>
      <template #footer>
        <el-button :disabled="importBusy" @click="importVisible=false">{{ importDone?'完成':'取消' }}</el-button>
        <el-button v-if="!importDone" type="primary" :disabled="!report?.success" :loading="importBusy" @click="commitImport">确认导入 {{ report?.success || 0 }} 人</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { get, post, put } from '@/utils/request'
import { ElMessage } from 'element-plus'
import { Download, Upload, Plus, Search, Refresh, Filter, Lock, CircleCheck, ArrowRight } from '@element-plus/icons-vue'
const people=ref<any[]>([]), departments=ref<any[]>([]), query=ref(''), loading=ref(false), saving=ref(false), visible=ref(false), form=ref<any>({}), error=ref('')
const columns=ref<{key:string,label:string,kind:string}[]>([])
const roles:Record<string,string>={employee:'普通员工（仅移动端）',manager:'项目管理权限',hr:'售后负责人',admin:'系统管理员'}
const permissionOptions=computed(()=>Object.fromEntries(Object.entries(roles).filter(([key])=>key!=='manager'||form.value.role==='manager')))
const rosterGroup=ref('employee')
const ordinaryPeople=computed(()=>people.value.filter(p=>p.role==='employee'))
const rosterPeople=computed(()=>people.value.filter(p=>rosterGroup.value==='management'?p.role!=='employee':p.role==='employee'))
const statuses=['在职','试用','离职'], statusFilter=ref('')
const statusLabel=(status:string)=>status==='试用'?'试用期':status||'待核对'
const statusClass=(status:string)=>status==='离职'?'left':status==='试用'?'probation':'employed'
function statusChanged(){form.value.is_active=form.value.status!=='离职'}
function closeEditor(done:()=>void){if(!saving.value)done()}
const formGroups=computed(()=>[
  {title:'基本信息',keys:['name','phone','position','company','status']},
  {title:'任职与个人资料',keys:['current_project','aftersales_leader','hire_date','leave_date','id_card_no','age','wecom_joined','days_employed']},
  {title:'装备与工服',keys:['clothing_size','company_computer','summer_uniform','reflective_vest','computer_advice']},
].map(group=>({...group,columns:group.keys.flatMap(key=>columns.value.filter(c=>c.key===key))})))
const positions=ref<any[]>([])
const positionOptions=computed(()=>{
 const choices=positions.value.map(p=>({...p,value:`${p.department_id||''}:${p.name}`,label:departments.value.length>1?`${p.department_name} / ${p.name}`:p.name}))
 const current=`${form.value.department_id||''}:${form.value.position}`
 if(form.value.position&&!choices.some(p=>p.value===current))choices.push({value:current,name:form.value.position,label:form.value.position,department_id:form.value.department_id,department_name:form.value.department_name})
 return choices
})
const selectedPosition=computed({get:()=>form.value.position?`${form.value.department_id||''}:${form.value.position}`:'',set:(value:string)=>{
 const option=positionOptions.value.find(p=>p.value===value)
 form.value.position=option?.name||''
 if(option){form.value.department_id=option.department_id;form.value.department_name=option.department_name==='未关联部门'?'':option.department_name}
}})
const columnFilters=ref<Record<string,string[]>>({})
const filterColumns=computed(()=>[...columns.value,{key:'account_state',label:'账号状态',kind:'text'},...(rosterGroup.value==='management'?[{key:'role',label:'系统权限',kind:'text'}]:[])])
const activeFilterCount=computed(()=>Object.values(columnFilters.value).filter(values=>values.length).length)
function filterValue(person:any,key:string){
 if(key==='role')return roles[person.role]||person.role
 if(key==='account_state')return person.status==='离职'?'已锁定':person.is_active?'已开通':'已停用'
 if(key==='status')return statusLabel(person.status)
 return String(person[key]??'')
}
function filterOptions(key:string){return [...new Set(rosterPeople.value.map(person=>filterValue(person,key)))].sort((a,b)=>a.localeCompare(b,'zh-CN',{numeric:true}))}
const filtered=computed(()=>rosterPeople.value.filter(p=>Object.entries(columnFilters.value).every(([key,values])=>!values.length||values.includes(filterValue(p,key))) && (!statusFilter.value || p.status===statusFilter.value) && [p.name,p.phone,p.employee_no,p.department_name,p.current_project,p.company].join(' ').includes(query.value.trim())).sort((a,b)=>Number(a.status==='离职')-Number(b.status==='离职')))
const page=ref(1), pageSize=ref(20)
const pagedPeople=computed(()=>filtered.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value))
watch(rosterGroup,()=>{page.value=1;query.value='';statusFilter.value='';columnFilters.value={}})
watch(columnFilters,()=>{page.value=1},{deep:true})
watch([query,statusFilter,pageSize],()=>{page.value=1})
watch(()=>filtered.value.length,total=>{page.value=Math.min(page.value,Math.max(1,Math.ceil(total/pageSize.value)))})
const importVisible=ref(false), importBusy=ref(false), importDone=ref(false), importFile=ref<File|null>(null), report=ref<any>(null), importError=ref('')
function message(e:any, fallback:string) { return typeof e.response?.data?.detail==='string'?e.response.data.detail:fallback }
async function load(){loading.value=true;try{const [p,d,c,j]=await Promise.all([get('/field/people'),get('/departments'),get('/field/roster/columns'),get<any[]>('/field/positions')]);people.value=p;departments.value=Array.isArray(d)?d:(d.items||[]);columns.value=c;positions.value=j}catch(e:any){ElMessage.error(message(e,'人员资料加载失败'))}finally{loading.value=false}}
async function edit(row?:any){error.value='';form.value=row?{...row,password:''}:{name:'',employee_no:'',phone:'',position:'',role:'employee',status:'在职',is_active:true,password:'',wecom_userid:'',age:null,days_employed:null};if(!form.value.department_id&&form.value.department_name){const matches=departments.value.filter(d=>d.name===form.value.department_name);if(matches.length===1)form.value.department_id=matches[0].id}if(!form.value.department_id){const available=departments.value.filter(d=>d.is_active!==false);if(available.length===1){form.value.department_id=available[0].id;form.value.department_name=available[0].name}}visible.value=true;try{positions.value=await get<any[]>('/field/positions')}catch(e){ElMessage.error('岗位列表加载失败，请刷新后重试')}}
async function save(){if(saving.value)return;error.value='';if(!form.value.name?.trim()||!form.value.phone?.trim()){error.value='请填写姓名和联系电话';return}if(!form.value.id&&!/^1[3-9]\d{9}$/.test(form.value.phone.trim())){error.value='请输入 11 位有效手机号';return}saving.value=true;try{const data={...form.value,name:form.value.name.trim(),phone:form.value.phone.trim(),password:form.value.password||null};data.id?await put('/field/people/'+data.id,data):await post('/field/people',data);ElMessage.success(data.status==='离职'?'已保存，离职账号已锁定':data.id?'资料已更新':'人员已录入，手机号账号已开通');visible.value=false;await load()}catch(e:any){error.value=message(e,'请检查资料格式；如设置密码，至少 8 位')}finally{saving.value=false}}
function openImport(){importVisible.value=true;importFile.value=null;report.value=null;importError.value='';importDone.value=false}
function selectFile(event:Event){importFile.value=(event.target as HTMLInputElement).files?.[0]||null;report.value=null;importDone.value=false;importError.value=''}
async function downloadTemplate(){try{const blob=await get<Blob>('/field/roster/template',undefined,{responseType:'blob'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='售后名册模板.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch{ElMessage.error('模板下载失败，请稍后重试')}}
async function runImport(preview:boolean){if(!importFile.value)return;importBusy.value=true;importError.value='';try{const data=new FormData();data.append('file',importFile.value);report.value=await post('/field/roster/import',data,{params:{preview},timeout:300000});if(!preview){importDone.value=true;await load()}}catch(e:any){importError.value=message(e,'导入失败，请检查文件或稍后重试')}finally{importBusy.value=false}}
const previewImport=()=>runImport(true)
const commitImport=()=>runImport(false)
onMounted(load)
</script>
<style scoped>
.people-page { max-width: 1600px; margin: 0 auto; }
.people-header { display:flex; justify-content:space-between; align-items:center; gap:24px; margin: 0 0 20px; }
.people-header h1 { display:flex; align-items:center; gap:12px; font-size:25px; letter-spacing:-.5px; margin:0 0 8px; color:#252b45; }
.people-header h1 span { font-size:14px; font-weight:500; letter-spacing:0; background:#eceafa; color:#6555cf; border-radius:9px; padding:4px 10px; }
.header-actions { display:flex; gap:10px; flex-wrap:wrap; }
.header-actions .el-button { margin:0; border-radius:9px; height:38px; }
.roster-groups { margin-bottom:20px; }
.people-controls { display:flex; justify-content:space-between; align-items:center; gap:20px; margin-bottom:16px; border-bottom:1px solid #e7e9f1; padding-bottom:12px; }
.status-filters { display:flex; gap:5px; flex-wrap:wrap; }
.status-filters button { display:flex; gap:8px; align-items:center; background:transparent; border:0; border-radius:8px; padding:10px 13px; color:#788195; cursor:pointer; font:inherit; font-size:13px; }
.status-filters button.selected { color:#5b4ed1; background:#eeecfb; font-weight:600; }
.status-filters button span { font-size:11px; opacity:.8; }
.status-filters button:focus-visible { outline:2px solid #7164de; outline-offset:2px; }
.search-actions { display:flex; gap:10px; width:460px; max-width:100%; }
.filter-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }.column-filters { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 16px; max-height:55vh; overflow:auto; }.column-filters .el-select { width:100%; }
.people-content { min-height:220px; background:#fff; border-radius:12px; overflow:hidden; }
.people-list-scroll { overflow:auto; max-height:calc(100vh - 360px); min-height:160px; }
.people-list { width:100%; min-width:850px; border-collapse:separate; border-spacing:0; table-layout:fixed; font-size:13px; }
.people-list th { position:sticky; top:0; z-index:1; background:#f5f6fb; padding:13px 18px; color:#788098; text-align:center; font-size:12px; font-weight:500; border-bottom:1px solid #ebedf4; }
.people-list th:nth-child(1) { width:21%; }.people-list th:nth-child(2) { width:13%; }.people-list th:nth-child(3) { width:15%; }.people-list th:nth-child(4) { width:17%; }.people-list th:nth-child(5) { width:17%; }.people-list th:nth-child(6) { width:10%; }.people-list th:nth-child(7) { width:7%; }
.people-list.management-list th { width:auto; }.people-list.management-list th:last-child { width:7%; }
.people-list td { padding:8px 18px; border-bottom:1px solid #f0f1f6; color:#4f586e; text-align:center; vertical-align:middle; overflow-wrap:anywhere; }
.people-list tbody tr:last-child td { border-bottom:0; }.people-list tbody tr:hover { background:#f9f8ff; }
.people-list .row-action { text-align:center; white-space:nowrap; }.row-action .el-button { font-size:12px; }.row-action .el-icon { margin-left:3px; }
.person-name { font-size:13px; font-weight:600; color:#30364c; }
.phone-cell { font-variant-numeric:tabular-nums; }.row-primary { display:block; }.row-secondary { display:block; color:#7c8497; font-size:11px; margin-top:4px; }.project-name { color:#666080; }.project-name.unassigned { color:#9096a6; }
.list-footer { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; padding:16px 18px; border-top:1px solid #ebedf4; }.list-footer>span { color:#788098; font-size:12px; }
.employment-status { display:inline-flex; align-items:center; gap:5px; font-size:11px; border-radius:6px; padding:3px 7px; white-space:nowrap; line-height:1.5; }
.employment-status::before { content:''; width:5px; height:5px; border-radius:50%; background:currentColor; }
.employed { background:#eaf7f0; color:#398565; }.probation { background:#fff4de; color:#a47a24; }.left { background:#fde9e7; color:#c03936; }
.account-state { display:flex; justify-content:center; align-items:center; gap:5px; color:#688578; font-size:12px; white-space:nowrap; }.account-state.locked { color:#858b9a; }.people-list tr.departed { background:#fff8f7; }.people-list tr.departed:hover { background:#fff0ee; }.departed .person-name,.departed .account-state { color:#b74743; }
.person-form { width:100%; }
.form-section { margin-bottom:24px; }.form-section h3 { display:flex; justify-content:space-between; gap:12px; font-size:15px; color:#394057; border-bottom:1px solid #eef0f5; padding-bottom:12px; margin:0 0 20px; }.form-section h3 span { font-size:11px; font-weight:400; color:#a0a5b4; }
.person-form .el-select,.person-form .el-input-number { width:100%; }.account-settings { border:1px solid #e9eaf1; padding:16px; border-radius:10px; margin-bottom:20px; }.account-settings h3 { color:#69718a; font-size:14px; margin:0 0 20px; }
.editor-footer { display:flex; justify-content:flex-end; align-items:center; gap:12px; }.editor-footer>span { font-size:12px; color:#9a9fb0; }
.import-preview { max-height:420px; overflow:auto; }.import-person { border:1px solid #e9eaf1; border-radius:10px; padding:14px; margin:10px 0; }.import-person summary { display:flex; flex-wrap:wrap; gap:12px; align-items:center; cursor:pointer; font-size:12px; color:#737c90; }.import-person summary strong { color:#3b4259; }.import-line { color:#a0a5b4; }.preview-fields { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; font-size:12px; }.preview-fields dt { color:#9299a9; }.preview-fields dd { color:#444c63; margin:5px 0; overflow-wrap:anywhere; }
.roster-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 20px; }
@media (max-width: 1100px) { .people-header,.people-controls { align-items:flex-start; flex-direction:column; }.search-actions { width:100%; } }
@media (max-width: 600px) { .roster-fields { grid-template-columns: 1fr; }.people-header h1 { font-size:24px; }.editor-footer { flex-wrap:wrap; }.preview-fields { grid-template-columns:1fr 1fr; } }
.import-summary { margin: 16px 0; }
.import-error { color: #b42318; }
.file-picker { position: relative; overflow: hidden; padding: 7px 15px; border: 1px solid #dcdfe6; border-radius: 4px; cursor: pointer; white-space: nowrap; }
.file-picker input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
</style>
