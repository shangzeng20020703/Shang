<template>
  <div v-loading="loading">
    <div class="page-intro">
      <div><h1>项目库</h1><p>选项目，配置人员与规则，员工按项目打卡。</p></div>
      <div><el-button v-if="data.can_manage" @click="router.push('/attendance?tab=rules')">配置考勤规则</el-button><el-button :loading="sourceLoading" @click="refresh">刷新项目</el-button></div>
    </div>
    <div class="workflow"><span><b>1</b>选择项目</span><span><b>2</b>负责人配置人员与规则</span><span><b>3</b>员工自动匹配考勤</span></div>
    <el-alert v-if="error || sourceError" :title="error || sourceError" type="error" :closable="false" show-icon class="notice"/>
    <el-alert v-if="data.can_manage && sourceConfigured === false" title="项目来源尚未连接，请联系管理员配置 Tegene 读取凭据。" type="info" :closable="false" class="notice"/>
    <section class="surface">
      <div class="field-toolbar">
        <el-input v-model="query" placeholder="搜索项目名称、编号、负责人" clearable style="max-width:340px"/>
        <el-select v-model="stage" aria-label="配置状态" style="width:155px"><el-option label="全部项目" value="all"/><el-option label="已配置规则" value="ready"/><el-option label="待配置规则" value="pending"/></el-select>
        <span class="list-count">共 {{rows.length}} 个项目</span>
      </div>
      <el-table :data="pagedRows" class="field-table" empty-text="暂无匹配项目" @row-click="openProject">
        <el-table-column prop="code" label="项目编号" width="140"/>
        <el-table-column prop="name" label="项目名称" min-width="260"/>
        <el-table-column label="负责人" width="120"><template #default="{row}">{{row.manager_name || '待指定'}}</template></el-table-column>
        <el-table-column label="考勤规则" min-width="180"><template #default="{row}"><span v-if="row.rule_name">{{row.rule_name}}</span><el-tag v-else type="info">待配置</el-tag></template></el-table-column>
        <el-table-column label="参与人员" width="100"><template #default="{row}">{{row.member_count || 0}} 人</template></el-table-column>
        <el-table-column label="操作" width="115" fixed="right"><template #default="{row}"><el-button link type="primary" @click.stop="openProject(row)">{{canManage(row) ? '配置项目' : '查看项目'}}</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="12" :total="rows.length" layout="prev, pager, next" class="pagination"/>
      <p class="muted">项目资料来自 Tegene；考勤规则统一在「考勤管理 → 考勤规则」中配置，项目直接选用。</p>
    </section>

    <el-drawer v-model="visible" :title="canManage(selected) ? '项目配置' : '项目详情'" size="min(860px, 96vw)" :close-on-click-modal="!saving">
      <template v-if="selected">
        <div class="project-heading"><span class="muted">{{selected.code}}</span><h2>{{selected.name}}</h2></div>
        <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon class="notice"/>
        <section class="config-section">
          <h3>负责人 · 考勤规则</h3>
          <el-form label-position="top">
            <div class="config-grid">
              <el-form-item label="项目负责人">
                <el-select v-model="config.manager_id" filterable placeholder="指定负责人" :disabled="!data.can_manage || saving">
                  <el-option v-for="p in managers" :key="p.id" :label="`${p.name} · ${p.employee_no}`" :value="p.id" :disabled="!p.is_active"/>
                </el-select>
              </el-form-item>
              <el-form-item label="考勤规则">
                <el-select v-model="config.rule_id" filterable placeholder="选择已配置并启用的考勤规则" :disabled="!canManage(selected) || saving">
                  <el-option v-for="r in data.rules.filter((r:any)=>r.is_active || r.id === config.rule_id)" :key="r.id" :label="r.name" :value="r.id" :disabled="!r.is_active"/>
                </el-select>
              </el-form-item>
            </div>
            <p v-if="chosenRule" class="rule-summary">{{ruleSummary(chosenRule)}}</p>
            <p class="muted">此处只关联已有规则，无需重复录入时间、定位或照片要求。<el-button v-if="data.can_manage" link type="primary" @click="router.push('/attendance?tab=rules')">前往考勤规则配置</el-button></p>
            <p v-if="canManage(selected)" class="muted">已打卡的分段保留原规则；新的项目分段使用当前规则。</p>
            <el-button v-if="canManage(selected)" type="primary" :loading="saving" :disabled="!config.manager_id" @click="saveConfig">保存配置</el-button>
          </el-form>
        </section>
        <section class="config-section">
          <div class="section-title"><h3>参与人员 <small>{{members.length}}</small></h3><el-checkbox v-model="showHistory">显示历史</el-checkbox></div>
          <template v-if="canManage(selected)">
            <p v-if="!localProject?.rule_id" class="muted">先保存项目打卡规则，再分配人员。</p>
            <p v-else-if="configDirty" class="muted">请先保存上面的配置，再分配人员。</p>
            <el-form v-else label-position="top" class="members-form">
              <el-form-item label="添加人员"><el-select v-model="memberForm.employee_ids" multiple filterable placeholder="可选择多名人员" :disabled="saving"><el-option v-for="p in data.people" :key="p.id" :value="p.id" :label="`${p.name} · ${p.phone || p.employee_no}`" :disabled="data.assignments.some((a:any)=>a.employee_id===p.id && a.is_current)"/></el-select></el-form-item>
              <div class="member-actions"><span class="muted">添加后立即生效；已有项目的人员请使用调动。</span><el-button type="primary" plain :loading="saving" :disabled="!memberForm.employee_ids.length" @click="addMembers">添加到项目</el-button><el-button :disabled="saving" @click="openMemberImport">导入项目人员</el-button></div>
            </el-form>
          </template>
          <el-table :data="members" empty-text="暂无参与人员" class="field-table">
            <el-table-column prop="employee_name" label="人员" width="120"/>
            <el-table-column label="参与时间" min-width="210"><template #default="{row}">{{formatTime(row.started_at)}} — {{row.ended_at?formatTime(row.ended_at):'至今'}}</template></el-table-column>
            <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="row.is_current ? 'success' : 'info'">{{memberState(row)}}</el-tag></template></el-table-column>
            <el-table-column v-if="canManage(selected)" label="操作" width="150"><template #default="{row}"><template v-if="row.is_current || (row.status==='active' && row.start_date>today)"><el-button v-if="row.is_current" link type="primary" :disabled="saving" @click="openTransfer(row)">调动</el-button><el-button link type="danger" :disabled="saving" @click="cancelMember(row)">移出</el-button></template></template></el-table-column>
          </el-table>
        </section>
      </template>
    </el-drawer>
    <el-dialog v-model="transferVisible" title="调动人员" width="480px" :close-on-click-modal="!saving">
      <p>{{transferPerson?.employee_name}} · {{selected?.name}}</p>
      <el-select v-model="transferProject" filterable placeholder="选择目标项目" style="width:100%"><el-option v-for="p in transferTargets" :key="p.id" :value="p.id" :label="p.name" /></el-select>
      <p class="muted">调动立即生效；原项目打卡保留，新打卡使用目标项目规则。</p>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" />
      <template #footer><el-button @click="transferVisible=false">取消</el-button><el-button type="primary" :disabled="!transferProject" :loading="saving" @click="transferMember">确认调动</el-button></template>
    </el-dialog>
    <el-dialog v-model="importVisible" title="导入项目人员" width="min(760px,94vw)" :close-on-click-modal="!saving">
      <p>按姓名、手机号匹配已有花名册人员。</p>
      <div class="field-toolbar"><el-button @click="downloadMemberTemplate">下载模板</el-button><input type="file" accept=".xlsx,.xls,.csv" aria-label="项目人员文件" :disabled="saving" @change="selectMemberFile" /><el-button :disabled="!memberFile" :loading="saving" @click="importMembers(true)">预览</el-button></div>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" />
      <el-table v-if="importReport" :data="importReport.rows" max-height="340"><el-table-column prop="name" label="姓名" /><el-table-column prop="phone" label="手机号" /><el-table-column label="核对结果" min-width="240"><template #default="{row}">{{row.error || '可加入项目'}}</template></el-table-column></el-table>
      <template #footer><el-button @click="importVisible=false">取消</el-button><el-button type="primary" :disabled="!importReport?.success || !!importReport.failed" :loading="saving" @click="importMembers(false)">确认导入</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import {ref, computed, onMounted, watch} from 'vue'
import {useRouter} from 'vue-router'
import {get, post, put} from '@/utils/request'
import {ElMessage, ElMessageBox} from 'element-plus'
const router = useRouter()
const data = ref<any>({projects:[], assignments:[], people:[], rules:[], managed_project_ids:[]})
const loading=ref(false), saving=ref(false), error=ref(''), sourceError=ref(''), sourceLoading=ref(false), sourceConfigured=ref<boolean|null>(null)
const sourceProjects=ref<any[]>([]), query=ref(''), stage=ref('all'), page=ref(1), visible=ref(false), selected=ref<any>(null), formError=ref(''), showHistory=ref(false)
const config=ref<any>({manager_id:undefined, rule_id:undefined})
const memberForm=ref<{employee_ids:number[]}>({employee_ids:[]})
const transferVisible=ref(false), transferPerson=ref<any>(null), transferProject=ref<number>(), importVisible=ref(false), memberFile=ref<File|null>(null), importReport=ref<any>(null)
const transferTargets=computed(()=>data.value.projects.filter((p:any)=>p.id!==selected.value?.id && p.rule_id && canManage(p)))
const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
const upstreamConfig={timeout:30000,skipErrorReport:true,skipDefaultErrorHandler:true}
const catalog=computed(()=>{
  const ids = new Set(sourceProjects.value.map(p=>p.local_project_id).filter(Boolean))
  return [...sourceProjects.value.map(p=>({...data.value.projects.find((local:any)=>local.id===p.local_project_id), external_id:p.id, code:p.projectNo, name:p.name})), ...data.value.projects.filter((p:any)=>!ids.has(p.id))]
})
const rows=computed(()=>catalog.value.filter((p:any)=>(stage.value==='all' || (stage.value==='ready' ? !!p.rule_id : !p.rule_id)) && [p.name,p.code,p.manager_name].join(' ').toLowerCase().includes(query.value.trim().toLowerCase())))
const pagedRows=computed(()=>rows.value.slice((page.value-1)*12,page.value*12))
const localProject=computed(()=>data.value.projects.find((p:any)=>p.id===selected.value?.id))
const members=computed(()=>data.value.assignments.filter((a:any)=>a.project_id===selected.value?.id && (showHistory.value || a.is_current)))
const managers=computed(()=>{
  const people=data.value.people.filter((p:any)=>p.can_be_manager || p.id===config.value.manager_id)
  if(config.value.manager_id && !people.some((p:any)=>p.id===config.value.manager_id)) people.push({id:config.value.manager_id,name:localProject.value?.manager_name || '原负责人',employee_no:'已停用',is_active:false})
  return people
})
const chosenRule=computed(()=>data.value.rules.find((r:any)=>r.id===config.value.rule_id))
const configDirty=computed(()=>config.value.manager_id!==localProject.value?.manager_id || config.value.rule_id!==localProject.value?.rule_id)
function canManage(p:any){return p && p.is_active!==false && (data.value.can_manage || data.value.managed_project_ids.includes(p.id))}
function memberState(a:any){return a.is_current?'参与中':a.start_date>today && a.status==='active'?'待参与':'已结束'}
function formatTime(value:string){return value?new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value)?value:value+'Z').toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—'}
function ruleSummary(r:any){const methods=[r.require_gps?'GPS 定位':'',r.require_wifi?'Wi-Fi':''].filter(Boolean);return [(r.clock_in_time&&r.clock_out_time)?`${r.clock_in_time.slice(0,5)} — ${r.clock_out_time.slice(0,5)}`:'按规则时段打卡', methods.length?methods.join(['all','both'].includes(r.check_method_mode)?' + ':' / '):'无需定位或 Wi-Fi 校验',r.require_photo?'需要照片':''].filter(Boolean).join(' · ')}
function message(e:any){return typeof e.response?.data?.detail==='string'?e.response.data.detail:'操作失败，请检查必填项后重试'}
async function loadLocal(){data.value=await get('/field/directory')}
async function refresh(){
  loading.value=true;error.value='';sourceError.value=''
  try{await loadLocal();if(data.value.can_manage){sourceLoading.value=true;try{const result=await get('/field/tegene/projects',undefined,upstreamConfig);sourceProjects.value=result.projects;sourceConfigured.value=result.configured}catch(e){sourceProjects.value=[];sourceError.value=message(e)+'；已配置的项目仍可使用。'}finally{sourceLoading.value=false}}}catch(e){error.value=message(e)}finally{loading.value=false}
}
function openProject(row:any){selected.value=row;config.value={manager_id:row.manager_id,rule_id:row.rule_id};memberForm.value={employee_ids:[]};showHistory.value=false;formError.value='';visible.value=true}
async function saveConfig(){
  saving.value=true;formError.value=''
  try{
    const payload={manager_id:config.value.manager_id,rule_id:config.value.rule_id || null}
    const result=selected.value.external_id && data.value.can_manage?await post(`/field/tegene/projects/${selected.value.external_id}/configure`,payload,upstreamConfig):await put(`/field/projects/${selected.value.id}/configuration`,payload)
    selected.value.id=result.id
    const source=sourceProjects.value.find(p=>p.id===selected.value.external_id);if(source)source.local_project_id=result.id
    await loadLocal();config.value={manager_id:localProject.value.manager_id,rule_id:localProject.value.rule_id};ElMessage.success('项目配置已保存')
  }catch(e){formError.value=message(e)}finally{saving.value=false}
}
async function addMembers(){
  saving.value=true;formError.value=''
  try{await post(`/field/projects/${selected.value.id}/members`,{employee_ids:memberForm.value.employee_ids});memberForm.value={employee_ids:[]};await loadLocal();ElMessage.success('人员已加入项目，打卡规则将自动匹配')}catch(e){formError.value=message(e)}finally{saving.value=false}
}
async function cancelMember(row:any){
  try{await ElMessageBox.confirm('移出后停止此项目打卡，已有记录保留。未完成的考勤标记为待核对。','移出项目')}catch{return}
  saving.value=true;formError.value=''
  try{await post(`/field/assignments/${row.id}/remove`);await loadLocal()}catch(e){formError.value=message(e)}finally{saving.value=false}
}
function openTransfer(row:any){transferPerson.value=row;transferProject.value=undefined;formError.value='';transferVisible.value=true}
async function transferMember(){saving.value=true;formError.value='';try{await post(`/field/assignments/${transferPerson.value.id}/transfer`,{project_id:transferProject.value});await loadLocal();transferVisible.value=false;ElMessage.success('调动已生效')}catch(e){formError.value=message(e)}finally{saving.value=false}}
function openMemberImport(){memberFile.value=null;importReport.value=null;formError.value='';importVisible.value=true}
function selectMemberFile(event:Event){memberFile.value=(event.target as HTMLInputElement).files?.[0] || null;importReport.value=null}
function downloadMemberTemplate(){const url=URL.createObjectURL(new Blob(['\ufeff姓名,手机号\r\n'],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='项目人员模板.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function importMembers(preview:boolean){if(!memberFile.value)return;saving.value=true;formError.value='';try{const file=new FormData();file.append('file',memberFile.value);importReport.value=await post(`/field/projects/${selected.value.id}/members/import`,file,{params:{preview},timeout:300000});if(!preview){await loadLocal();importVisible.value=false;ElMessage.success('项目人员已导入')}}catch(e){formError.value=message(e)}finally{saving.value=false}}
watch([query,stage],()=>page.value=1)
onMounted(refresh)
</script>
<style scoped>
.workflow{display:flex;gap:30px;flex-wrap:wrap;margin:0 0 24px;color:#567180;font-size:13px}.workflow span{display:flex;align-items:center;gap:9px}.workflow b{display:grid;place-items:center;width:25px;height:25px;border-radius:50%;background:#e7f3f2;color:#147e73}
.field-toolbar{justify-content:flex-start;flex-wrap:wrap}.list-count{margin-left:auto;color:#76909b;font-size:13px}.pagination{justify-content:flex-end;margin-top:20px}.muted{color:#70838f;font-size:12px;line-height:1.7}.notice{margin-bottom:18px}.project-heading h2{font-size:23px;margin:8px 0 24px}.config-section{border-top:1px solid #e6edf0;padding:22px 0}.config-section h3{margin:0 0 18px;font-size:16px}.config-section small{color:#8496a2;margin-left:8px}.config-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.config-section .el-select{width:100%}.rule-summary{background:#f1f7f7;padding:12px 16px;color:#316d66;border-radius:7px;font-size:13px}.section-title,.member-actions{display:flex;justify-content:space-between;align-items:center;gap:12px}.members-form{background:#f7f9fb;border:1px solid #e9eef1;padding:18px;border-radius:8px;margin-bottom:18px}.members-form :deep(.el-date-editor){width:100%}@media(max-width:640px){.config-grid{grid-template-columns:1fr;gap:0}.member-actions{align-items:flex-start;flex-direction:column}}
</style>
