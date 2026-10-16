<template>
  <div class="rotation-page" v-loading="loading">
    <header><div><h1>现场轮休</h1><p>个人六上休一周期跨项目延续；当天按所属项目规则核对打卡。已发布排班的调整依次审核后生效。</p></div><el-button @click="load">刷新</el-button></header>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <section class="panel toolbar">
      <el-select v-model="projectId" placeholder="选择要排班的项目" @change="projectChanged" style="width:390px"><el-option v-for="p in projects" :key="p.id" :label="`${p.code} · ${p.name}${p.pending_member_count ? ` · ${p.pending_member_count}人待生效` : !p.rule_id ? ' · 待配置规则' : ''}`" :value="p.id" /></el-select>
      <el-button @click="changeWeek(-1)">上一周</el-button><strong>{{weekStartDate}} 至 {{weekEndDate}}</strong><el-button @click="changeWeek(1)">下一周</el-button><el-button @click="goThisWeek">本周</el-button>
      <el-select v-model="filterEmployee" clearable placeholder="全部参与人员" @change="reloadCalendar" style="width:200px"><el-option v-for="p in members" :key="p.id" :label="p.name" :value="p.id" /></el-select>
    </section>
    <p v-if="!projects.length">暂无可查看的项目，请先在项目库配置现场轮休规则及参与人员。</p>
    <section v-if="selectedProject && (!rotationRule || projectPendingMembers.length)" class="panel setup-panel">
      <h2>{{selectedProject.name}} · 排班准备</h2>
      <p v-if="!selectedProject.rule_id">项目已分配的人员会先保存为“待配置”。请为本项目新建考勤规则并选择“现场轮休”；规则保存、人员启用后才能进入个人排班。</p>
      <p v-else-if="!rotationRule">本项目当前规则不是“现场轮休”，请先修改本项目规则。</p>
      <p v-else>本项目还有 {{projectPendingMembers.length}} 位人员待统一启用。处理项目规则页提示的冲突后重试；待生效人员不会出现在下方参与人员中，也不会被发布排班。</p>
      <div v-if="projectPendingMembers.length" class="pending-people"><el-tag v-for="person in projectPendingMembers" :key="person.id" type="warning" effect="plain">{{person.employee_name || name(person.employee_id)}}{{person.is_leader_auto ? ' · 项目组长' : ' · 组员'}} · {{selectedProject.rule_id ? '待启用' : '待配置规则'}}</el-tag></div>
      <el-button v-if="selectedProject.can_manage" type="primary" @click="openProjectRule">{{selectedProject.rule_id ? '打开本项目规则设置' : '新建本项目现场轮休规则'}}</el-button>
      <p v-else class="hint">请联系本项目的售后经理或区域组长完成规则设置和人员启用。</p>
    </section>
    <el-tabs v-model="tab">
      <el-tab-pane label="个人排班" name="calendar">
        <section class="panel" v-if="canManage && projectId">
          <h2>本周个人排班</h2>
          <p v-if="!rotationRule" class="warning">请先完成上方的项目规则设置；当前没有可发布的排班人员。</p>
          <p v-if="!publishableWeek" class="hint">本周没有可发布的未来日期，可查看下方历史排班；请切换到本周或未来一周继续排班。</p>
          <p v-else class="hint">本次发布 {{publishStart}} 至 {{weekEndDate}}；已过去的日期及规则尚未生效的日期不会纳入。</p>
          <el-form label-width="100px" :disabled="saving">
            <el-form-item label="参与人员"><el-select v-model="plan.employee_ids" multiple placeholder="本周已分配人员默认全选，可取消选择" style="width:100%" @change="invalidatePreview"><el-option v-for="p in eligibleMembers" :key="p.id" :label="p.name" :value="p.id" /></el-select></el-form-item>
            <el-form-item label="周期起点"><el-date-picker v-model="plan.cycle_start" value-format="YYYY-MM-DD" @change="invalidatePreview" /><span class="hint">首次排班从这天起上六天、第七天休；已建立的个人周期跨项目延续，不因调班或交接重置。</span></el-form-item>
            <el-form-item label="原因"><el-input v-model="plan.reason" maxlength="500" /></el-form-item>
            <el-form-item><el-button :disabled="!canPreviewPlan" @click="preview">预览本周排班</el-button><el-button type="primary" :disabled="!previewData.revision || !previewData.rows.length" @click="publish">确认发布 {{previewData.rows.length || ''}} 人日</el-button></el-form-item>
          </el-form>
          <el-table v-if="previewData.rows.length" :data="previewData.rows" max-height="350" size="small"><el-table-column label="人员"><template #default="{row}">{{name(row.employee_id)}}</template></el-table-column><el-table-column prop="date" label="日期"/><el-table-column prop="cycle_start" label="个人周期起点"/><el-table-column label="原定"><template #default="{row}">{{labels[row.original_kind]}}</template></el-table-column><el-table-column label="发布安排"><template #default="{row}"><el-select :model-value="row.kind" @change="(kind:any)=>editPreview(row,kind)"><el-option label="上班" value="work"/><el-option label="正常轮休" value="rest"/></el-select></template></el-table-column><el-table-column label="班别（仅展示）" width="135"><template #default="{row}"><el-select :model-value="row.shift_label || ''" :disabled="row.kind!=='work'" @change="(value:any)=>editPreviewShift(row,value)"><el-option label="未指定" value=""/><el-option label="白班" value="day"/><el-option label="夜班" value="night"/></el-select></template></el-table-column></el-table>
          <p v-if="previewData.rows.length" class="hint">每行只修改对应人员的当天安排；原定轮休日与个人周期保留。</p>
        </section>
        <section class="panel">
          <div class="actions"><h2>已发布排班</h2><el-button v-if="canSchedule" :disabled="!selectedDays.length" @click="adjustDialog=true">申请调整所选日期</el-button></div>
          <el-table :data="data.days" @selection-change="(rows:any[])=>selectedDays=rows" max-height="600" empty-text="该时段尚未发布排班，打卡按待确认记录">
            <el-table-column v-if="canSchedule" type="selection" width="42"/><el-table-column prop="employee_name" label="人员" width="130"/><el-table-column prop="date" label="日期" width="120"/>
            <el-table-column label="原定安排" width="130"><template #default="{row}">{{labels[row.original_kind]}}</template></el-table-column>
            <el-table-column prop="label" label="当前安排" width="130"/><el-table-column label="班别" width="95"><template #default="{row}">{{shiftLabel(row.shift_label)}}</template></el-table-column><el-table-column prop="required_hours" label="应出勤工时" width="110"/><el-table-column prop="reason" label="调整原因"/>
          </el-table>
        </section>
      </el-tab-pane>
      <el-tab-pane label="申请与出勤确认" name="claims">
        <section class="panel"><h2>提交调班申请</h2><p class="hint">攒休确认和调休由员工本人在手机端发起，再交售后经理审批。</p><el-form label-width="100px">
          <el-form-item label="人员"><el-select v-model="claim.employee_id"><el-option v-for="p in members" :key="p.id" :label="p.name" :value="p.id" /></el-select></el-form-item>
          <el-form-item label="申请类型"><el-select v-model="claim.kind"><el-option label="调班（逐级审核）" value="schedule"/></el-select></el-form-item>
          <el-form-item label="日期"><el-date-picker v-model="claimRange" type="daterange" value-format="YYYY-MM-DD" /></el-form-item>
          <el-form-item label="安排"><el-select v-model="claim.dayKind"><el-option label="上班" value="work"/><el-option label="正常轮休" value="rest"/></el-select></el-form-item>
          <el-form-item v-if="claim.kind==='schedule' && claim.dayKind==='work'" label="班别（仅展示）"><el-select v-model="claim.shiftLabel"><el-option label="未指定" value=""/><el-option label="白班" value="day"/><el-option label="夜班" value="night"/></el-select></el-form-item>
          <el-form-item label="原因"><el-input v-model="claim.reason" maxlength="500"/></el-form-item>
          <el-form-item><el-button type="primary" :loading="saving" :disabled="!claim.employee_id || !projectId" @click="submitClaim">提交申请</el-button></el-form-item>
        </el-form></section>
        <section class="panel"><h2>申请记录</h2><el-table :data="data.claims" empty-text="暂无申请"><el-table-column prop="employee_name" label="人员" width="120"/><el-table-column label="类型" width="110"><template #default="{row}">{{claimLabels[row.kind]}}</template></el-table-column><el-table-column label="日期"><template #default="{row}">{{row.days.map((d:any)=>`${d.date}${d.shift_label?` · ${shiftLabel(d.shift_label)}`:''}`).join('、')}}</template></el-table-column><el-table-column label="状态" min-width="200"><template #default="{row}">{{statusLabels[row.status] || row.status}}<span v-if="row.status==='pending' && row.next_approval"> · 待{{approvalRoleLabel(row.next_approval.role)}}{{row.next_approval.project_id ? `（项目${row.next_approval.project_id}）` : ''}}</span><div v-if="row.approval_steps?.length" class="approval-steps">{{row.approval_steps.map((step:any)=>`${approvalRoleLabel(step.role)}${step.project_id?`·项目${step.project_id}`:''}：${stepStatusLabel(step.status)}`).join(' → ')}}</div></template></el-table-column><el-table-column label="出勤 / 额度" min-width="190"><template #default="{row}"><span v-if="row.current_facts">{{Number(row.current_facts.hours).toFixed(2)}} / {{row.current_facts.required_hours}}小时，建议{{row.current_facts.suggested_days}}天<br/></span>{{row.days_amount}}天 <b v-if="row.needs_review" class="warning">打卡已变化，需复核</b></template></el-table-column><el-table-column prop="reason" label="原因"/><el-table-column prop="review_note" label="审核说明"/><el-table-column label="处理" width="165"><template #default="{row}"><el-button v-if="row.status==='pending' && row.can_review" link type="primary" @click="openReview(row,'approve')">批准</el-button><el-button v-if="row.status==='pending' && row.can_review" link type="danger" @click="openReview(row,'reject')">驳回</el-button><el-button v-if="row.status==='approved' && row.can_review" link type="danger" @click="openReview(row,'revoke')">撤销</el-button></template></el-table-column></el-table></section>
      </el-tab-pane>
      <el-tab-pane label="待休余额与流水" name="ledger"><section class="panel"><h2>可用待休</h2><p class="hint">正常轮休不扣余额，批准额外调休才扣减。这里单独记录现场轮休来源。</p><el-table :data="data.balances"><el-table-column label="人员"><template #default="{row}">{{row.employee_name || name(row.employee_id)}}</template></el-table-column><el-table-column prop="days" label="可用天数"/></el-table><h2>流水</h2><el-table :data="data.ledger"><el-table-column label="人员"><template #default="{row}">{{name(row.employee_id)}}</template></el-table-column><el-table-column prop="claim_id" label="申请号"/><el-table-column label="变动天数"><template #default="{row}">{{row.delta_units/2}}</template></el-table-column><el-table-column label="变动后余额"><template #default="{row}">{{row.balance_units/2}}</template></el-table-column><el-table-column prop="reason" label="原因"/><el-table-column prop="created_at" label="时间"/></el-table></section></el-tab-pane>
    </el-tabs>
    <el-dialog v-model="adjustDialog" title="申请调整已发布排班" width="500px"><p>提交后，项目组长、区域组长、售后经理依次审核；全部批准后才改变排班。原定轮休日及周期保留。</p><el-select v-model="adjustKind"><el-option label="上班" value="work"/><el-option label="正常轮休" value="rest"/></el-select><el-select v-if="adjustKind==='work'" v-model="adjustShiftLabel" placeholder="班别（仅展示）" style="margin-top:12px"><el-option label="保留原班别" value=""/><el-option label="不标注班别" value="none"/><el-option label="白班" value="day"/><el-option label="夜班" value="night"/></el-select><el-input v-model="adjustReason" type="textarea" placeholder="填写调整原因" maxlength="500"/><template #footer><el-button @click="adjustDialog=false">返回</el-button><el-button type="primary" :loading="saving" @click="adjust">提交调班申请</el-button></template></el-dialog>
    <el-dialog v-model="reviewDialog" title="处理轮休申请" width="520px"><p>{{reviewRow?.employee_name}} · {{claimLabels[reviewRow?.kind]}} · {{reviewRow?.days.map((d:any)=>d.date+' '+labels[d.kind]).join('、')}}</p><template v-if="reviewRow?.kind==='credit' && reviewAction==='approve'"><p>实际{{Number(reviewRow.current_facts?.hours||0).toFixed(2)}}小时 / 要求{{reviewRow.current_facts?.required_hours}}小时</p><p v-if="reviewRow.current_facts?.issues?.length" class="warning">打卡异常：{{reviewRow.current_facts.issues.join('、')}}</p><el-select v-model="reviewDays"><el-option v-for="n in [0,0.5,1]" :key="n" :label="`${n}天`" :value="n"/></el-select><p class="hint">不足半天时，填写理由后可确认零或半天。缺卡须先补齐。</p></template><el-input v-model="reviewNote" type="textarea" placeholder="填写审核或撤销原因" maxlength="500"/><template #footer><el-button @click="reviewDialog=false">返回</el-button><el-button type="primary" :loading="saving" @click="review">确认{{reviewAction==='approve'?'批准':reviewAction==='reject'?'驳回':'撤销'}}</el-button></template></el-dialog>
  </div>
</template>
<script setup lang="ts">
import {ref,computed,onMounted,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import {get,post} from '@/utils/request'
const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
function addDays(day:string,n:number){const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)}
function monday(day:string){const weekday=new Date(day+'T12:00:00Z').getUTCDay();return addDays(day,-((weekday+6)%7))}
const route=useRoute()
const router=useRouter()
const loading=ref(false),saving=ref(false),error=ref(''),tab=ref('calendar'),directory=ref<any>({projects:[],assignments:[],pending_members:[],people:[],rules:[],managed_project_ids:[]})
const projectId=ref<number>(),filterEmployee=ref<number>(),weekAnchor=ref(today)
const data=ref<any>({days:[],claims:[],balances:[],ledger:[]}),previewData=ref<any>({rows:[],revision:''}),selectedDays=ref<any[]>([])
const plan=ref<any>({employee_ids:[],cycle_start:today,reason:'周期排班',edits:[],person_edits:[]})
const weekStartDate=computed(()=>monday(weekAnchor.value))
const weekEndDate=computed(()=>addDays(weekStartDate.value,6))
const range=computed(()=>[weekStartDate.value,weekEndDate.value])
const projects=computed<any[]>(()=>directory.value.projects)
const selectedProject=computed(()=>projects.value.find((p:any)=>p.id===projectId.value))
const projectPendingMembers=computed<any[]>(()=>Array.isArray(directory.value.pending_members) ? directory.value.pending_members.filter((person:any)=>person.project_id===projectId.value) : [])
const canSchedule=computed(()=>!!(selectedProject.value?.can_schedule ?? (directory.value.can_manage || directory.value.schedule_project_ids?.includes(projectId.value))))
const canManage=canSchedule
const selectedRule=computed(()=>directory.value.rules.find((r:any)=>r.id===selectedProject.value?.rule_id))
const rotationRule=computed(()=>selectedRule.value?.rule_type==='rotation')
const publishStart=computed(()=>{const dates=[today,weekStartDate.value,String(selectedRule.value?.effective_date || '')].sort();return dates[dates.length-1] || today})
const publishableWeek=computed(()=>publishStart.value<=weekEndDate.value)
const memberAssignments=computed<any[]>(()=>directory.value.assignments.filter((a:any)=>a.project_id===projectId.value && (a.is_current || a.status==='active')))
const members=computed<any[]>(()=>{const ids=new Set(memberAssignments.value.map((a:any)=>a.employee_id));return directory.value.people.filter((p:any)=>ids.has(p.id))})
const eligibleMembers=computed<any[]>(()=>{
  if(!publishableWeek.value)return []
  const ids=new Set(memberAssignments.value.filter((a:any)=>(!a.start_date || a.start_date<=weekEndDate.value) && (!a.end_date || a.end_date>=publishStart.value)).map((a:any)=>a.employee_id))
  return members.value.filter((p:any)=>ids.has(p.id))
})
const canPreviewPlan=computed(()=>rotationRule.value && publishableWeek.value && plan.value.employee_ids.length>0 && !!plan.value.cycle_start && plan.value.cycle_start<=publishStart.value && !!String(plan.value.reason || '').trim())
function name(id:number){return directory.value.people.find((p:any)=>p.id===id)?.name || `人员${id}`}
function openProjectRule(){if(projectId.value)router.push({path:'/attendance',query:{tab:'rules',project_id:projectId.value}})}
const labels:any={work:'上班',rest:'正常轮休',leave:'调休',half_leave:'半天调休',pending:'待确认'},claimLabels:any={schedule:'调班',credit:'出勤确认',leave:'调休'},statusLabels:any={pending:'待审批',approved:'已批准',rejected:'已驳回',revoked:'已撤销'}
function shiftLabel(value:string|null|undefined){return value==='day'?'白班':value==='night'?'夜班':'未标注'}
function approvalRoleLabel(role:string){return ({project_leader:'项目组长',regional_lead:'区域组长',aftersales_manager:'售后经理',admin:'系统管理员'} as Record<string,string>)[role]||role}
function stepStatusLabel(status:string){return ({pending:'待处理',approved:'已通过',rejected:'已驳回',skipped:'已跳过'} as Record<string,string>)[status]||status}
function message(e:any){return typeof e.response?.data?.detail==='string'?e.response.data.detail:e.message||'操作失败，请重试'}
async function run(fn:()=>Promise<void>){saving.value=true;error.value='';try{await fn()}catch(e){error.value=message(e)}finally{saving.value=false}}
async function reloadCalendar(){if(!projectId.value)return;data.value=await get('/rotation/calendar',{project_id:projectId.value,employee_id:filterEmployee.value,start:range.value[0],end:range.value[1]})}
async function load(){loading.value=true;try{
  directory.value=await get('/field/directory')
  const requested=Number(route.query.project_id || 0)
  if(requested && projects.value.some((p:any)=>p.id===requested))projectId.value=requested
  else if(!projectId.value || !projects.value.some((p:any)=>p.id===projectId.value))projectId.value=projects.value[0]?.id
  plan.value.employee_ids=eligibleMembers.value.map((p:any)=>p.id)
  plan.value.cycle_start=publishStart.value
  invalidatePreview()
  await reloadCalendar()
}catch(e){error.value=message(e)}finally{loading.value=false}}
function invalidatePreview(){previewData.value={rows:[],revision:''}}
async function projectChanged(){plan.value.employee_ids=eligibleMembers.value.map((p:any)=>p.id);plan.value.cycle_start=publishStart.value;plan.value.person_edits=[];filterEmployee.value=undefined;invalidatePreview();await reloadCalendar()}
async function changeWeek(offset:number){weekAnchor.value=addDays(weekStartDate.value,offset*7);plan.value.employee_ids=eligibleMembers.value.map((p:any)=>p.id);plan.value.cycle_start=publishStart.value;plan.value.person_edits=[];invalidatePreview();await reloadCalendar()}
async function goThisWeek(){weekAnchor.value=today;plan.value.employee_ids=eligibleMembers.value.map((p:any)=>p.id);plan.value.cycle_start=publishStart.value;plan.value.person_edits=[];invalidatePreview();await reloadCalendar()}
function planBody(){return {...plan.value,start_date:publishStart.value,end_date:weekEndDate.value,member_days_only:true}}
async function preview(){await run(async()=>{previewData.value=await post(`/rotation/projects/${projectId.value}/preview`,planBody())})}
async function editPreview(row:any,kind:string){const existing=plan.value.person_edits.find((e:any)=>e.date===row.date && e.employee_id===row.employee_id);plan.value.person_edits=[...plan.value.person_edits.filter((e:any)=>e.date!==row.date || e.employee_id!==row.employee_id),{employee_id:row.employee_id,date:row.date,kind,shift_label:kind==='work'?(existing?.shift_label ?? row.shift_label ?? null):null}];await preview()}
async function editPreviewShift(row:any,value:string){const existing=plan.value.person_edits.find((e:any)=>e.date===row.date && e.employee_id===row.employee_id);plan.value.person_edits=[...plan.value.person_edits.filter((e:any)=>e.date!==row.date || e.employee_id!==row.employee_id),{employee_id:row.employee_id,date:row.date,kind:existing?.kind || row.kind || 'work',shift_label:value||null}];await preview()}
async function publish(){await ElMessageBox.confirm('发布后按个人日期判断出勤，确认所选人员和日期正确。','发布排班');await run(async()=>{await post(`/rotation/projects/${projectId.value}/publish?revision=${previewData.value.revision}`,planBody());invalidatePreview();await reloadCalendar();ElMessage.success('排班已发布')})}
const adjustDialog=ref(false),adjustKind=ref('work'),adjustShiftLabel=ref(''),adjustReason=ref('')
async function adjust(){
  if(!adjustReason.value.trim())return ElMessage.warning('请填写原因')
  await run(async()=>{
    const result:any=await post(`/rotation/projects/${projectId.value}/adjust`,{
      changes:selectedDays.value.map(r=>({employee_id:r.employee_id,date:r.date,kind:adjustKind.value,expected_revision:r.revision,...(adjustKind.value==='work' && adjustShiftLabel.value ? {shift_label:adjustShiftLabel.value==='none'?null:adjustShiftLabel.value} : {})})),
      reason:adjustReason.value,
    })
    adjustDialog.value=false
    selectedDays.value=[]
    await reloadCalendar()
    ElMessage.success(`已提交 ${result?.count || result?.claims?.length || 1} 条调班申请，等待逐级审核`)
  })
}
const claim=ref({employee_id:undefined as number|undefined,kind:'schedule',dayKind:'work',shiftLabel:'',reason:''}),claimRange=ref([today,today])
watch(()=>claim.value.kind,kind=>{claim.value.dayKind=kind==='leave'?'leave':'work'})
function requestDays(){if(!claimRange.value?.length)throw new Error('请选择日期');const [start,end]=claimRange.value;if(end<start||end>addDays(start,61))throw new Error('每次申请最多62天');const rows=[];for(let d=start;d<=end;d=addDays(d,1))rows.push({date:d,kind:claim.value.dayKind,...(claim.value.kind==='schedule' && claim.value.dayKind==='work' && claim.value.shiftLabel ? {shift_label:claim.value.shiftLabel} : {})});return rows}
async function submitClaim(){if(!claim.value.reason.trim())return ElMessage.warning('请填写原因');await run(async()=>{await post('/rotation/claims',{employee_id:claim.value.employee_id,project_id:projectId.value,kind:claim.value.kind,days:requestDays(),reason:claim.value.reason});await reloadCalendar();ElMessage.success('申请已提交')})}
const reviewDialog=ref(false),reviewRow=ref<any>(),reviewAction=ref('approve'),reviewDays=ref(0),reviewNote=ref('')
function openReview(row:any,action:string){reviewRow.value=row;reviewAction.value=action;reviewDays.value=row.current_facts?.suggested_days||0;reviewNote.value='';reviewDialog.value=true}
async function review(){if(!reviewNote.value.trim())return ElMessage.warning('请填写审核原因');await run(async()=>{await post(`/rotation/claims/${reviewRow.value.id}/review`,{action:reviewAction.value,note:reviewNote.value,...(reviewRow.value.kind==='credit'?{days:reviewDays.value}:{}),...(reviewRow.value.kind==='schedule' && reviewRow.value.status==='pending' && Number.isInteger(reviewRow.value.next_approval?.step_index)?{expected_step:reviewRow.value.next_approval.step_index}:{})});reviewDialog.value=false;await reloadCalendar();ElMessage.success('已处理')})}
watch(()=>route.query.project_id,async value=>{const requested=Number(value || 0);if(requested && projects.value.some((p:any)=>p.id===requested) && requested!==projectId.value){projectId.value=requested;await projectChanged()}})
onMounted(load)
</script>
<style scoped>
.rotation-page{max-width:1500px;margin:auto;color:#263449}header,.actions,.toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}h1{font-size:26px;margin:0}h2{font-size:18px;margin:0 0 18px}p{line-height:1.7}header p,.hint{color:#718096}.hint{font-size:13px;margin-left:12px}.panel{background:#fff;border:1px solid #e3e9f1;border-radius:14px;padding:24px;margin:20px 0}.warning{color:#bd6505}.el-textarea{margin-top:16px}.toolbar{justify-content:flex-start}.actions h2{margin:0}.approval-steps{font-size:12px;color:#718096;margin-top:4px;white-space:normal}.setup-panel{border-color:#f2d9a1;background:#fffbf3}.setup-panel h2{margin-bottom:8px}.pending-people{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}
</style>
