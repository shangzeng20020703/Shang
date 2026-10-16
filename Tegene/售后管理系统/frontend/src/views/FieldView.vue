<template>
  <div v-loading="loading">
    <div class="page-intro">
      <div><h1>项目库</h1><p>先分配项目人员，再建立现场轮休规则，最后按周发布个人排班。</p></div>
      <div><el-button v-if="canManageGlobalRules || canManageProjectRules" @click="router.push('/attendance?tab=rules')">配置考勤规则</el-button><el-button :loading="sourceLoading" @click="refresh">刷新项目</el-button></div>
    </div>
    <div class="workflow"><span><b>1</b>选择项目并分配人员</span><span><b>2</b>新建现场轮休规则</span><span><b>3</b>预览个人排班</span><span><b>4</b>按周发布</span></div>
    <el-alert v-if="error || sourceError" :title="error || sourceError" type="error" :closable="false" show-icon class="notice"/>
    <el-alert v-if="canManageGlobalRules && sourceConfigured === false" title="项目来源尚未连接，请联系管理员配置 Tegene 读取凭据。" type="info" :closable="false" class="notice"/>
    <section class="surface">
      <div class="field-toolbar">
        <el-input v-model="query" placeholder="搜索项目名称、编号、项目组长" clearable style="max-width:340px"/>
        <el-select v-model="stage" aria-label="配置状态" style="width:155px"><el-option label="全部项目" value="all"/><el-option label="已配置规则" value="ready"/><el-option label="待配置规则" value="pending"/></el-select>
        <span class="list-count">共 {{rows.length}} 个项目</span>
      </div>
      <el-table :data="pagedRows" class="field-table" empty-text="暂无匹配项目" @row-click="openProject">
        <el-table-column prop="code" label="项目编号" width="140"/>
        <el-table-column prop="name" label="项目名称" min-width="260"/>
        <el-table-column label="项目组长" width="120"><template #default="{row}">{{row.manager_name || '待指定'}}</template></el-table-column>
        <el-table-column label="考勤规则" min-width="180"><template #default="{row}"><span v-if="row.rule_name">{{row.rule_name}}</span><el-tag v-else type="info">待配置</el-tag></template></el-table-column>
        <el-table-column label="参与人员" min-width="205"><template #default="{row}"><template v-if="savedMemberCount(row)"><strong>已保存 {{savedMemberCount(row)}} 人</strong><div class="member-count-detail">{{row.member_count || 0}} 人已生效<span v-if="pendingCount(row)"> · {{pendingCount(row)}} 人{{row.rule_id ? '待启用' : '待建立规则后启用'}}</span></div></template><span v-else class="muted">尚未分配</span></template></el-table-column>
        <el-table-column label="操作" width="115" fixed="right"><template #default="{row}"><el-button link type="primary" @click.stop="openProject(row)">{{canManage(row) ? '配置项目' : '查看项目'}}</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="PAGE_SIZE" :total="rows.length" layout="prev, pager, next" class="pagination"/>
      <p class="muted">每页 50 行。项目资料来自 Tegene；人员与本项目专用规则保存在售后系统。</p>
    </section>
    <section v-if="transferRequests.length" class="surface transfer-list">
      <h3>跨项目交接申请</h3>
      <el-table :data="transferRequests" empty-text="暂无待处理交接">
        <el-table-column prop="employee_name" label="人员" width="120"/><el-table-column prop="from_project_name" label="原项目"/><el-table-column prop="to_project_name" label="目标项目"/><el-table-column prop="effective_date" label="生效日期" width="125"/><el-table-column prop="status" label="状态" width="120"/>
        <el-table-column label="操作" width="150"><template #default="{row}"><template v-if="row.can_review && row.status==='pending'"><el-button link type="primary" @click="reviewTransfer(row,true)">确认交接</el-button><el-button link type="danger" @click="reviewTransfer(row,false)">驳回</el-button></template></template></el-table-column>
      </el-table>
    </section>

    <el-drawer v-model="visible" :title="canManage(selected) ? '项目配置' : '项目详情'" size="min(860px, 96vw)" :close-on-click-modal="!saving">
      <template v-if="selected">
        <div class="project-heading"><span class="muted">{{selected.code}}</span><h2>{{selected.name}}</h2></div>
        <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon class="notice"/>
        <div v-if="pendingMembers.length && !localProject?.rule_id" class="saved-roster notice">
          <el-alert :title="`已保存${savedMemberCount(localProject)}人，其中${pendingMembers.length}人待建立规则后启用`" type="success" :closable="false" show-icon/>
          <el-button v-if="canManage(selected) && hasAssignedGroupMember && !configDirty" type="primary" @click="openProjectRule">下一步：新建现场轮休规则</el-button>
        </div>
        <section class="config-section">
          <h3>1. 指定项目组长</h3>
          <el-form label-position="top">
            <el-form-item label="项目组长（每项目一位）">
              <el-select v-model="config.manager_id" filterable placeholder="从项目组长候选人中选择" :disabled="!canManage(selected) || saving" style="max-width:420px">
                <el-option v-for="p in managers" :key="p.id" :label="`${p.name} · ${p.employee_no}`" :value="p.id" :disabled="!p.is_active"/>
              </el-select>
            </el-form-item>
            <p class="muted">保存后，项目组长自动列入待配置人员，无需再次添加。建立规则并启用人员前，不能按本项目打卡。</p>
            <el-button v-if="canManage(selected)" type="primary" :loading="saving" :disabled="!config.manager_id || !configDirty" @click="saveConfig">保存项目组长</el-button>
          </el-form>
        </section>
        <section class="config-section">
          <div class="section-title"><h3>分配组员 <small>已保存 {{savedMemberCount(localProject)}} 人（{{activeMemberCount}} 人已生效，{{pendingMembers.length}} 人待启用）</small></h3><el-checkbox v-model="showHistory">显示历史</el-checkbox></div>
          <template v-if="canManage(selected)">
            <p v-if="!selected.id || configDirty" class="muted">请先保存上方的项目组长，再分配组员。</p>
            <p v-else-if="!localProject?.rule_id" class="muted">现在可先分配人员；他们会处于“待配置”，建立并保存本项目规则后统一启用。</p>
            <el-form label-position="top" class="members-form">
              <el-form-item label="选择组员（可多选）"><el-select v-model="memberForm.employee_ids" multiple filterable placeholder="仅显示组员" :disabled="saving"><el-option v-for="p in memberCandidates" :key="p.id" :value="p.id" :label="`${p.name} · ${p.phone || p.employee_no}`" :disabled="data.assignments.some((a:any)=>a.employee_id===p.id && a.is_current) || pendingMembers.some((a:any)=>a.employee_id===p.id)"/></el-select></el-form-item>
              <div class="member-actions"><span class="muted">规则建立前先保存为待配置；已有项目的人员请使用调动。</span><el-button type="primary" plain :loading="saving" :disabled="!memberAssignmentReady || !memberForm.employee_ids.length" @click="addMembers">{{localProject?.rule_id ? '分配组员到项目' : '保存待配置组员'}}</el-button><el-button :disabled="saving || !memberAssignmentReady" @click="openMemberImport">批量导入组员</el-button></div>
            </el-form>
          </template>
          <el-table v-if="pendingMembers.length" :data="pendingMembers" class="field-table" empty-text="暂无待配置人员">
            <el-table-column prop="employee_name" label="待配置人员" width="150"/>
            <el-table-column label="状态"><template #default>{{localProject?.rule_id ? '名单已保存，待统一启用；尚不能打卡' : '名单已保存，待建立规则后启用；尚不能打卡'}}</template></el-table-column>
            <el-table-column v-if="canManage(selected)" label="操作" width="170"><template #default="{row}"><span v-if="row.is_leader_auto" class="muted">更换组长后自动更新</span><el-button v-else link type="danger" :disabled="saving" @click="removePendingMember(row)">移出</el-button></template></el-table-column>
          </el-table>
          <el-table :data="members" empty-text="暂无参与人员" class="field-table">
            <el-table-column prop="employee_name" label="人员" width="120"/>
            <el-table-column label="参与时间" min-width="210"><template #default="{row}">{{formatTime(row.started_at)}} — {{row.ended_at?formatTime(row.ended_at):'至今'}}</template></el-table-column>
            <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="row.is_current && row.can_punch ? 'success' : 'info'">{{memberState(row)}}</el-tag></template></el-table-column>
            <el-table-column v-if="canManage(selected)" label="操作" width="150"><template #default="{row}"><template v-if="row.is_current || (row.status==='active' && row.start_date>today)"><el-button v-if="row.is_current" link type="primary" :disabled="saving" @click="openTransfer(row)">调动</el-button><el-button link type="danger" :disabled="saving" @click="cancelMember(row)">移出</el-button></template></template></el-table-column>
          </el-table>
        </section>
        <section class="config-section">
          <h3>2. 建立本项目考勤规则</h3>
          <p v-if="chosenRule" class="rule-summary">{{chosenRule.name}} · {{ruleSummary(chosenRule)}}</p>
          <p v-if="!localProject?.rule_id" class="muted">请新建规则并选择“现场轮休”，设置每日工时、打卡地点等要求。保存后待配置人员会统一生效；若有冲突，会列出人员供你处理。</p>
          <p v-else class="muted">本项目已有考勤规则。修改规则仅作用于本项目，原有打卡记录保留。</p>
          <div class="member-actions">
            <el-button v-if="canManage(selected)" type="primary" :disabled="!selected.id || configDirty || (!localProject?.rule_id && !hasAssignedGroupMember)" @click="openProjectRule">{{localProject?.rule_id ? '编辑本项目规则' : '新建现场轮休规则'}}</el-button>
            <el-button v-if="canManage(selected) && localProject?.rule_id && pendingMembers.length" :loading="saving" @click="activatePendingMembers">重试启用待配置人员</el-button>
          </div>
          <p v-if="canManage(selected) && !localProject?.rule_id && !hasAssignedGroupMember" class="muted">请先在上方分配至少一名组员，再新建规则。</p>
          <el-alert v-if="activationError" :title="activationError" type="error" :closable="false" show-icon class="notice"/>
        </section>
        <section class="config-section">
          <h3>3. 个人排班</h3>
          <p class="muted">本项目人员生效后，打开个人排班。按北京时间周一至周日预览六上休一建议，调整后逐周确认发布。</p>
          <el-button type="primary" :disabled="!rotationRuleReady || !activeMemberCount || !!pendingMembers.length" @click="openRotation">打开本项目个人排班</el-button>
        </section>
        <section class="config-section">
          <h3>区域组长负责范围</h3>
          <p class="muted">区域组长可以查看所有项目；只有被售后经理分配为主责或协管的项目可以修改。每个项目设置一位主责，协管可留空。</p>
          <el-form label-position="top">
            <div class="config-grid">
              <el-form-item label="主责区域组长"><el-select v-model="regionalForm.primary_lead_id" clearable filterable placeholder="选择主责" :disabled="!data.can_assign_regionals || saving"><el-option v-for="p in regionalLeads" :key="p.id" :label="p.name" :value="p.id" /></el-select></el-form-item>
              <el-form-item label="协管区域组长（可选）"><el-select v-model="regionalForm.assistant_lead_ids" multiple filterable placeholder="可留空" :disabled="!data.can_assign_regionals || saving"><el-option v-for="p in regionalLeads" :key="p.id" :label="p.name" :value="p.id" :disabled="p.id===regionalForm.primary_lead_id" /></el-select></el-form-item>
            </div>
            <el-button v-if="data.can_assign_regionals" type="primary" :loading="saving" :disabled="!selected.id" @click="saveRegionalLeads">保存区域分配</el-button>
          </el-form>
        </section>
      </template>
    </el-drawer>
    <el-dialog v-model="transferVisible" title="调动人员" width="480px" :close-on-click-modal="!saving">
      <p>{{transferPerson?.employee_name}} · {{selected?.name}}</p>
      <el-select v-model="transferProject" filterable placeholder="选择目标项目" style="width:100%"><el-option v-for="p in transferTargets" :key="p.id" :value="p.id" :label="p.name" /></el-select>
      <el-date-picker v-model="transferDate" type="date" value-format="YYYY-MM-DD" :disabled-date="(date:Date)=>date.getTime()<new Date(today+'T00:00:00').getTime()" style="width:100%;margin-top:12px" />
      <el-input v-model="transferReason" maxlength="500" type="textarea" placeholder="填写交接原因" style="margin-top:12px" />
      <p class="muted">按选定日期申请交接；跨区域项目由原项目和目标项目的区域组长分别确认，生效前原项目记录保留。</p>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" />
      <template #footer><el-button @click="transferVisible=false">取消</el-button><el-button type="primary" :disabled="!transferProject || !transferDate || !transferReason.trim()" :loading="saving" @click="transferMember">提交交接申请</el-button></template>
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
import {get, post, put, del} from '@/utils/request'
import {ElMessage, ElMessageBox} from 'element-plus'
import {getWebAuthRoles,isWebAuthSuperuser} from '@/utils/authSession'
const router = useRouter()
const PAGE_SIZE = 50
const data = ref<any>({projects:[], assignments:[], pending_members:[], people:[], rules:[], managed_project_ids:[],regional_assignments:[],can_assign_regionals:false})
const loading=ref(false), saving=ref(false), error=ref(''), sourceError=ref(''), sourceLoading=ref(false), sourceConfigured=ref<boolean|null>(null)
const sourceProjects=ref<any[]>([]), query=ref(''), stage=ref('all'), page=ref(1), visible=ref(false), selected=ref<any>(null), formError=ref(''), activationError=ref(''), showHistory=ref(false)
const config=ref<any>({manager_id:undefined, rule_id:undefined})
const regionalForm=ref<{primary_lead_id:number|undefined,assistant_lead_ids:number[]}>({primary_lead_id:undefined,assistant_lead_ids:[]})
const regionalPeople=ref<any[]>([])
const transferRequests=ref<any[]>([])
const memberForm=ref<{employee_ids:number[]}>({employee_ids:[]})
const transferVisible=ref(false), transferPerson=ref<any>(null), transferProject=ref<number>(), transferDate=ref(''), transferReason=ref(''), importVisible=ref(false), memberFile=ref<File|null>(null), importReport=ref<any>(null)
const transferTargets=computed(()=>data.value.projects.filter((p:any)=>p.id!==selected.value?.id && p.rule_id && p.is_active!==false))
const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
const upstreamConfig={timeout:30000,skipErrorReport:true,skipDefaultErrorHandler:true}
const catalog=computed(()=>{
  const ids = new Set(sourceProjects.value.map(p=>p.local_project_id).filter(Boolean))
  return [...sourceProjects.value.map(p=>({...data.value.projects.find((local:any)=>local.id===p.local_project_id), external_id:p.id, code:p.projectNo, name:p.name})), ...data.value.projects.filter((p:any)=>!ids.has(p.id))]
})
const rows=computed(()=>catalog.value.filter((p:any)=>(stage.value==='all' || (stage.value==='ready' ? !!p.rule_id : !p.rule_id)) && [p.name,p.code,p.manager_name].join(' ').toLowerCase().includes(query.value.trim().toLowerCase())))
const pagedRows=computed(()=>rows.value.slice((page.value-1)*PAGE_SIZE,page.value*PAGE_SIZE))
const localProject=computed(()=>data.value.projects.find((p:any)=>p.id===selected.value?.id))
const canManageGlobalRules=computed(()=>isWebAuthSuperuser()||getWebAuthRoles().some(r=>['admin','hr'].includes(r)))
const canManageProjectRules=computed(()=>getWebAuthRoles().some(r=>['aftersales_manager','regional_lead'].includes(r)))
const regionalLeads=computed(()=>{
  const assigned=new Set([regionalForm.value.primary_lead_id,...regionalForm.value.assistant_lead_ids])
  return regionalPeople.value.filter((p:any)=>p.is_active!==false && (p.role==='regional_lead' || assigned.has(p.id)))
})
const members=computed(()=>data.value.assignments.filter((a:any)=>a.project_id===selected.value?.id && (showHistory.value || a.is_current)))
const pendingMembers=computed<any[]>(()=>Array.isArray(data.value.pending_members)?data.value.pending_members.filter((a:any)=>a.project_id===selected.value?.id):[])
function pendingCount(project:any){return Array.isArray(data.value.pending_members)?data.value.pending_members.filter((a:any)=>a.project_id===project.id).length:0}
function savedMemberCount(project:any){return project ? (project.member_count || 0)+pendingCount(project) : 0}
const memberCandidates=computed(()=>data.value.people.filter((p:any)=>p.is_active!==false && p.role==='employee' && p.field_staff_category==='member'))
const managers=computed(()=>{
  const people=data.value.people.filter((p:any)=>p.role==='employee' && p.field_staff_category==='leader_candidate' && p.is_active!==false)
  const incumbent=data.value.people.find((p:any)=>p.id===config.value.manager_id)
  if(incumbent && !people.some((p:any)=>p.id===incumbent.id)) people.push(incumbent)
  if(config.value.manager_id && !people.some((p:any)=>p.id===config.value.manager_id)) people.push({id:config.value.manager_id,name:localProject.value?.manager_name || '原负责人',employee_no:'已停用',is_active:false})
  return people
})
const chosenRule=computed(()=>data.value.rules.find((r:any)=>r.id===config.value.rule_id))
const rotationRuleReady=computed(()=>Boolean(localProject.value?.rule_id && data.value.rules.find((r:any)=>r.id===localProject.value.rule_id)?.rule_type==='rotation'))
const activeMemberCount=computed(()=>data.value.assignments.filter((a:any)=>a.project_id===selected.value?.id && a.is_current).length)
const hasAssignedGroupMember=computed(()=>pendingMembers.value.some((a:any)=>!a.is_leader_auto) || data.value.assignments.some((a:any)=>a.project_id===selected.value?.id && a.is_current && a.employee_id!==config.value.manager_id))
const configDirty=computed(()=>config.value.manager_id!==localProject.value?.manager_id || config.value.rule_id!==localProject.value?.rule_id)
const memberAssignmentReady=computed(()=>Boolean(selected.value?.id && config.value.manager_id && !configDirty.value))
function canManage(p:any){return p && p.is_active!==false && (p.can_manage ?? (data.value.can_manage || data.value.managed_project_ids.includes(p.id)))}
function memberState(a:any){return a.is_current?(a.can_punch?'参与中':rotationRuleReady.value?'待首次排班':'待生效'):a.start_date>today && a.status==='active'?'待参与':'已结束'}
function formatTime(value:string){return value?new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value)?value:value+'Z').toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—'}
function ruleSummary(r:any){const methods=[r.require_gps?'GPS 定位':'',r.require_wifi?'Wi-Fi':''].filter(Boolean);return [(r.clock_in_time&&r.clock_out_time)?`${r.clock_in_time.slice(0,5)} — ${r.clock_out_time.slice(0,5)}`:'按规则时段打卡', methods.length?methods.join(['all','both'].includes(r.check_method_mode)?' + ':' / '):'无需定位或 Wi-Fi 校验',r.require_photo?'需要照片':''].filter(Boolean).join(' · ')}
function message(e:any){return typeof e.response?.data?.detail==='string'?e.response.data.detail:'操作失败，请检查必填项后重试'}
async function loadLocal(){
  data.value=await get('/field/directory')
  const eligible=new Set(memberCandidates.value.map((p:any)=>p.id))
  memberForm.value.employee_ids=memberForm.value.employee_ids.filter(id=>eligible.has(id))
  if(data.value.can_assign_regionals){try{regionalPeople.value=await get('/field/people')}catch{regionalPeople.value=[]}}
  try{transferRequests.value=await get('/field/transfer-requests')}catch{transferRequests.value=[]}
}
async function refresh(){
  loading.value=true;error.value='';sourceError.value=''
  try{await loadLocal();if(canManageGlobalRules.value){sourceLoading.value=true;try{const result=await get('/field/tegene/projects',undefined,upstreamConfig);sourceProjects.value=result.projects;sourceConfigured.value=result.configured}catch(e){sourceProjects.value=[];sourceError.value=message(e)+'；已配置的项目仍可使用。'}finally{sourceLoading.value=false}}}catch(e){error.value=message(e)}finally{loading.value=false}
}
function openProject(row:any){selected.value=row;config.value={manager_id:row.manager_id,rule_id:row.rule_id};regionalForm.value={primary_lead_id:row.primary_regional_lead_id||undefined,assistant_lead_ids:[...(row.assistant_regional_lead_ids||[])]};memberForm.value={employee_ids:[]};showHistory.value=false;formError.value='';activationError.value='';visible.value=true}
function openProjectRule(){if(!selected.value?.id || configDirty.value)return;router.push({path:'/attendance',query:{tab:'rules',project_id:selected.value.id}})}
function openRotation(){if(!rotationRuleReady.value || pendingMembers.value.length)return;router.push({path:'/rotation',query:{project_id:selected.value.id}})}
async function saveRegionalLeads(){
  if(!selected.value?.id)return
  saving.value=true;formError.value=''
  try{await put(`/field/projects/${selected.value.id}/regional-leads`,{primary_lead_id:regionalForm.value.primary_lead_id||null,assistant_lead_ids:regionalForm.value.assistant_lead_ids.filter(id=>id!==regionalForm.value.primary_lead_id)});await loadLocal();selected.value={...selected.value,...localProject.value};ElMessage.success('区域组长分配已保存')}
  catch(e){formError.value=message(e)}finally{saving.value=false}
}
async function saveConfig(){
  saving.value=true;formError.value=''
  try{
    const payload={manager_id:config.value.manager_id,rule_id:localProject.value?.rule_id || null}
    const result=selected.value.external_id && data.value.can_manage?await post(`/field/tegene/projects/${selected.value.external_id}/configure`,payload,upstreamConfig):await put(`/field/projects/${selected.value.id}/configuration`,payload)
    selected.value.id=result.id
    const source=sourceProjects.value.find(p=>p.id===selected.value.external_id);if(source)source.local_project_id=result.id
    await loadLocal();config.value={manager_id:localProject.value.manager_id,rule_id:localProject.value.rule_id};selected.value={...selected.value,...localProject.value};ElMessage.success('项目组长已保存')
  }catch(e){formError.value=message(e)}finally{saving.value=false}
}
async function addMembers(){
  saving.value=true;formError.value=''
  try{await post(`/field/projects/${selected.value.id}/members`,{employee_ids:memberForm.value.employee_ids})}
  catch(e){formError.value=message(e);saving.value=false;return}
  memberForm.value={employee_ids:[]}
  try{await loadLocal();ElMessage.success(localProject.value?.rule_id?'人员已加入项目':`人员名单已保存：共${savedMemberCount(localProject.value)}人，待建立规则后启用`)}
  catch{ElMessage.warning('人员名单已保存，但列表刷新失败；请点击“刷新项目”核对')}
  finally{saving.value=false}
}
async function removePendingMember(row:any){
  saving.value=true;formError.value=''
  try{await del(`/field/projects/${selected.value.id}/pending-members/${row.id}`);await loadLocal();ElMessage.success('已移出待配置人员')}
  catch(e){formError.value=message(e)}finally{saving.value=false}
}
function activationConflictMessage(e:any){
  const detail=e.response?.data?.detail
  if(detail && typeof detail==='object' && Array.isArray(detail.conflicts)){
    const conflicts=detail.conflicts.map((item:any)=>`${item.employee_name || `人员${item.employee_id}`}${item.project_name ? `（${item.project_name}）` : ''}${item.reason ? `：${item.reason}` : ''}`)
    return `${detail.message || '待配置人员尚未生效'}：${conflicts.join('；')}`
  }
  return message(e)
}
async function activatePendingMembers(){
  if(!selected.value?.id || !localProject.value?.rule_id)return
  saving.value=true;activationError.value=''
  try{await post(`/field/projects/${selected.value.id}/members/activate`,{});await loadLocal();ElMessage.success('待配置人员已统一启用')}
  catch(e){activationError.value=activationConflictMessage(e);await loadLocal()}
  finally{saving.value=false}
}
async function cancelMember(row:any){
  try{await ElMessageBox.confirm('移出后停止此项目打卡，已有记录保留。未完成的考勤标记为待核对。','移出项目')}catch{return}
  saving.value=true;formError.value=''
  try{await post(`/field/assignments/${row.id}/remove`);await loadLocal()}catch(e){formError.value=message(e)}finally{saving.value=false}
}
function openTransfer(row:any){transferPerson.value=row;transferProject.value=undefined;transferDate.value=addDays(today,1);transferReason.value='';formError.value='';transferVisible.value=true}
function addDays(day:string,n:number){const date=new Date(day+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+n);return date.toISOString().slice(0,10)}
async function transferMember(){saving.value=true;formError.value='';try{await post(`/field/assignments/${transferPerson.value.id}/transfer-requests`,{project_id:transferProject.value,effective_date:transferDate.value,reason:transferReason.value.trim()});await loadLocal();transferVisible.value=false;ElMessage.success('交接申请已提交，等待负责人确认')}catch(e){formError.value=message(e)}finally{saving.value=false}}
async function reviewTransfer(row:any,approve:boolean){let note='';try{const result=await ElMessageBox.prompt(approve?'填写交接确认说明':'填写驳回原因','跨项目交接',{inputPlaceholder:'请填写原因',inputValidator:(value:string)=>!!value.trim()||'请填写原因'});note=result.value}catch{return}saving.value=true;try{await post(`/field/transfer-requests/${row.id}/review`,{approve,note:note.trim()});await loadLocal();ElMessage.success(approve?'已确认交接':'已驳回交接')}catch(e){error.value=message(e)}finally{saving.value=false}}
function openMemberImport(){memberFile.value=null;importReport.value=null;formError.value='';importVisible.value=true}
function selectMemberFile(event:Event){memberFile.value=(event.target as HTMLInputElement).files?.[0] || null;importReport.value=null}
function downloadMemberTemplate(){const url=URL.createObjectURL(new Blob(['\ufeff姓名,手机号\r\n'],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='项目人员模板.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function importMembers(preview:boolean){if(!memberFile.value)return;saving.value=true;formError.value='';try{const file=new FormData();file.append('file',memberFile.value);importReport.value=await post(`/field/projects/${selected.value.id}/members/import`,file,{params:{preview},timeout:300000});if(!preview){await loadLocal();importVisible.value=false;ElMessage.success(localProject.value?.rule_id?'项目人员已导入':'人员已批量保存为待配置')}}catch(e){formError.value=message(e)}finally{saving.value=false}}
watch([query,stage],()=>page.value=1)
watch(()=>rows.value.length,total=>{page.value=Math.min(page.value,Math.max(1,Math.ceil(total/PAGE_SIZE)))})
onMounted(refresh)
</script>
<style scoped>
.workflow{display:flex;gap:30px;flex-wrap:wrap;margin:0 0 24px;color:#567180;font-size:13px}.workflow span{display:flex;align-items:center;gap:9px}.workflow b{display:grid;place-items:center;width:25px;height:25px;border-radius:50%;background:#e7f3f2;color:#147e73}
.field-toolbar{justify-content:flex-start;flex-wrap:wrap}.list-count{margin-left:auto;color:#76909b;font-size:13px}.pagination{justify-content:flex-end;margin-top:20px}.muted,.member-count-detail{color:#70838f;font-size:12px;line-height:1.7}.notice{margin-bottom:18px}.project-heading h2{font-size:23px;margin:8px 0 24px}.saved-roster{display:flex;align-items:center;gap:12px}.saved-roster .el-alert{flex:1}.config-section{border-top:1px solid #e6edf0;padding:22px 0}.config-section h3{margin:0 0 18px;font-size:16px}.config-section small{color:#8496a2;margin-left:8px}.config-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.config-section .el-select{width:100%}.rule-summary{background:#f1f7f7;padding:12px 16px;color:#316d66;border-radius:7px;font-size:13px}.section-title,.member-actions{display:flex;justify-content:space-between;align-items:center;gap:12px}.members-form{background:#f7f9fb;border:1px solid #e9eef1;padding:18px;border-radius:8px;margin-bottom:18px}.members-form :deep(.el-date-editor){width:100%}@media(max-width:640px){.config-grid{grid-template-columns:1fr;gap:0}.member-actions,.saved-roster{align-items:flex-start;flex-direction:column}.saved-roster .el-alert{width:100%}}
.leader-join{border-top:1px solid #e9eef1;margin-top:16px;padding-top:16px}
</style>
