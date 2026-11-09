<template>
  <div class="rotation-page" v-loading="loading">
    <header><div><h1>个人排班与现场轮休</h1><p>先确认项目人员，再逐人确认排班，最后配置项目规则。每满 6 个有效工作日获得 1 天休息额度，余下进度跨项目保留。</p></div><el-button @click="load">刷新</el-button></header>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <section class="panel toolbar">
      <el-select v-model="projectId" placeholder="选择要排班的项目" @change="projectChanged" style="width:390px"><el-option v-for="p in projects" :key="p.id" :label="`${p.code} · ${p.name}${!p.rule_id ? ' · 待确认个人排班' : p.pending_member_count ? ` · ${p.pending_member_count}人待生效` : ''}`" :value="p.id" /></el-select>
      <el-button @click="changeWeek(-1)">上一周</el-button><strong>{{weekStartDate}} 至 {{weekEndDate}}</strong><el-button @click="changeWeek(1)">下一周</el-button><el-button @click="goThisWeek">本周</el-button>
      <el-select v-model="filterEmployee" clearable placeholder="全部参与人员" @change="reloadCalendar" style="width:200px"><el-option v-for="p in members" :key="p.id" :label="p.name" :value="p.id" /></el-select>
    </section>
    <p v-if="!projects.length">暂无可查看的项目，请先在项目库确认项目人员。</p>
    <section v-if="selectedProject && (preRule || !rotationRule || projectPendingMembers.length)" class="panel setup-panel">
      <h2>{{selectedProject.name}} · 排班准备</h2>
      <p v-if="preRule">组长和组员逐人选择开始日期、模板及每天安排，分别确认一个完整周期。规则保存前计划待生效，不允许打卡或计算休息额度；未来项目未知的日期可标为“待分配项目”。</p>
      <p v-else-if="!rotationRule">本项目当前规则不是“现场轮休”，请先修改本项目规则。</p>
      <p v-else>本项目还有 {{projectPendingMembers.length}} 位人员待统一启用。处理项目规则页提示的冲突后重试；待生效人员不会出现在下方参与人员中，也不会被发布排班。</p>
      <div v-if="projectPendingMembers.length" class="pending-people"><el-tag v-for="person in projectPendingMembers" :key="person.id" :type="preRule && !draftMissingIds.has(Number(person.employee_id)) ? 'success' : 'warning'" effect="plain">{{person.employee_name || name(person.employee_id)}}{{person.is_leader_auto ? ' · 项目组长' : ' · 组员'}} · {{preRule ? (draftMissingIds.has(Number(person.employee_id)) ? '个人周期待确认' : '个人周期已确认') : '待启用'}}</el-tag></div>
      <p v-if="preRule" class="hint">已确认 {{selectedProject.pre_rule_schedule_member_count || 0}} / {{projectPendingMembers.length}} 人的完整周期。{{selectedProject.pre_rule_schedule_ready ? '已满足建规则条件。' : `仍需确认：${draftMissingNames}。`}}</p>
      <el-button v-if="selectedProject.can_manage" type="primary" :disabled="preRule && !selectedProject.pre_rule_schedule_ready" @click="openProjectRule">{{selectedProject.rule_id ? '打开本项目规则设置' : '下一步：新建现场轮休规则'}}</el-button>
      <p v-else-if="preRule && selectedProject.pre_rule_schedule_ready" class="hint">个人排班已全部确认，请联系售后经理或区域组长配置本项目规则。</p>
      <p v-else-if="!preRule" class="hint">请联系本项目的售后经理或区域组长完成规则设置和人员启用。</p>
    </section>
    <section v-if="isAdmin" class="panel" aria-label="滚动休息额度生效设置">
      <h2>滚动休息额度生效设置</h2>
      <p class="hint">从指定日期起，现场轮休项目每累计 6 个有效工作日获得 1 天休息额度；旧余额和流水保留，不追溯重算。设置前不启用新计账方式。</p>
      <div class="policy-controls"><el-date-picker v-model="policyDate" value-format="YYYY-MM-DD" type="date" placeholder="选择生效日期"/><el-button type="primary" :loading="policyLoading" :disabled="!policyDate" @click="savePolicy">保存生效日期</el-button><span class="hint">当前：{{entitlementPolicy.enabled ? `已设置 ${entitlementPolicy.effective_date}` : '尚未启用'}}</span></div>
    </section>
    <section v-if="canReviewEntitlement && pendingWorkDays.length" class="panel" aria-label="待确认有效出勤">
      <h2>待确认的有效出勤</h2>
      <p class="hint">半天工时、异常打卡和已批准出差需确认后才进入个人六天进度；同一天不会重复计入。</p>
      <el-table :data="pendingWorkDays" max-height="330"><el-table-column prop="employee_name" label="人员" width="130"/><el-table-column prop="date" label="日期" width="120"/><el-table-column label="项目"><template #default="{row}">{{projects.find((p:any)=>p.id===row.project_id)?.name || `项目${row.project_id}`}}</template></el-table-column><el-table-column prop="suggested_days" label="建议计入天数" width="130"/><el-table-column prop="reason" label="原因"/><el-table-column label="操作" width="150"><template #default="{row}"><el-button link type="primary" :disabled="reviewingWorkDay" @click="reviewWorkDay(row,'approve')">确认</el-button><el-button link type="danger" :disabled="reviewingWorkDay" @click="reviewWorkDay(row,'reject')">不计入</el-button></template></el-table-column></el-table>
    </section>
    <el-tabs v-model="tab">
      <el-tab-pane label="个人排班" name="calendar">
        <section class="panel" v-if="canManage && projectId">
          <h2>逐人确认个人排班</h2>
          <p class="hint">每位员工独立设置开始日期和周期起点。上六休一、上十二休二、上十八休三会按所选日期范围重复；也可逐日自定义。请逐人保存，已发布日期的修改须提交调班申请。</p>
          <p v-if="!preRule && !rotationRule" class="warning">本项目当前规则不是现场轮休，不能确认新的轮休排班。</p>
          <p v-if="preRule && !projectPendingMembers.length" class="warning">请先在项目库确认组长及组员名单。</p>
          <el-form label-width="110px" :disabled="saving || (!preRule && !rotationRule)">
            <el-form-item label="参与人员"><el-select v-model="person.employee_id" placeholder="选择一位组长或组员" style="width:340px" @change="personChanged"><el-option v-for="p in eligibleMembers" :key="p.id" :label="p.name" :value="p.id" /></el-select><span class="hint">一次只确认一人，其他人的起点和安排互不影响。</span></el-form-item>
            <el-form-item label="排班模板"><el-select v-model="person.template" style="width:220px" @change="templateChanged"><el-option label="上 6 天休 1 天" value="six_one"/><el-option label="上 12 天休 2 天" value="twelve_two"/><el-option label="上 18 天休 3 天" value="eighteen_three"/><el-option label="自定义每天安排" value="custom"/></el-select></el-form-item>
            <el-form-item label="开始日期"><el-date-picker v-model="person.start_date" value-format="YYYY-MM-DD" @change="personStartChanged" /></el-form-item>
            <el-form-item label="结束日期"><el-date-picker v-model="person.end_date" value-format="YYYY-MM-DD" @change="personRangeChanged" /><span class="hint">至少覆盖一个完整模板周期；可延长并重复该模板。</span></el-form-item>
            <el-form-item label="周期起点"><el-date-picker v-model="person.cycle_start" value-format="YYYY-MM-DD" @change="personRangeChanged" /><span class="hint">换项目不重置个人周期，可早于本项目的开始日期。</span></el-form-item>
            <el-form-item label="原因"><el-input v-model="person.reason" maxlength="500" style="max-width:480px" @input="invalidatePersonPreview" /></el-form-item>
            <el-form-item><el-button :disabled="!canPreviewPerson" @click="previewPerson">预览此人排班</el-button><el-button type="primary" :disabled="!canConfirmPerson" @click="confirmPerson">{{preRule ? '确认此人待生效排班' : '确认并发布此人排班'}}</el-button></el-form-item>
          </el-form>
          <p v-if="person.employee_id && savedPersonPlans.length" class="hint">已确认的个人周期：<el-button v-for="saved in savedPersonPlans" :key="saved.id" link type="primary" @click="editSavedPlan(saved)">{{saved.start_date}} 至 {{saved.end_date}} · {{templateLabel(saved.template)}}</el-button><el-button link @click="newPersonPlan">新建下一段</el-button></p>
          <div v-if="person.employee_id && bindableDays.length" class="bindable-days"><h3>可接入本项目的待分配日期</h3><p class="hint">员工已完成项目交接或加入本项目后，可把原计划中的“待分配项目”日期接入本项目。</p><el-checkbox-group v-model="selectedBindableDates"><el-checkbox v-for="item in bindableDays" :key="`${item.plan_id}-${item.date}`" :label="`${item.plan_id}|${item.date}`">{{item.date}} · {{labels[item.kind]}}</el-checkbox></el-checkbox-group><el-button type="primary" plain :disabled="!selectedBindableDates.length || saving" @click="bindSelectedDays">接入所选日期</el-button></div>
          <el-table v-if="personRows.length" :data="personRows" max-height="470" size="small" row-key="date">
            <el-table-column prop="date" label="日期" width="115"/>
            <el-table-column label="安排" width="145"><template #default="{row}"><el-select :model-value="row.kind" @change="(value:any)=>editPersonDay(row.date,'kind',value)"><el-option label="上班" value="work"/><el-option label="休息" value="rest"/></el-select></template></el-table-column>
            <el-table-column label="项目" min-width="170"><template #default="{row}"><el-select :model-value="row.project_id" @change="(value:any)=>editPersonDay(row.date,'project_id',value)"><el-option :label="selectedProject?.name || '本项目'" :value="projectId"/><el-option label="待分配项目" :value="null"/></el-select></template></el-table-column>
            <el-table-column label="班别（仅展示）" width="145"><template #default="{row}"><el-select :model-value="row.shift_label || ''" :disabled="row.kind!=='work'" @change="(value:any)=>editPersonDay(row.date,'shift_label',value)"><el-option label="未指定" value=""/><el-option label="白班" value="day"/><el-option label="夜班" value="night"/></el-select></template></el-table-column>
            <el-table-column label="状态" width="150"><template #default="{row}">{{personStatusLabel(row.status || (preRule ? 'pending_rule' : 'preview'))}}</template></el-table-column>
          </el-table>
          <p v-if="personRows.length" class="hint">修改当天安排或项目后请重新预览，再确认保存。待分配项目日期保留个人周期，但不能打卡。</p>
        </section>
        <section class="panel">
          <div class="actions"><h2>已发布排班</h2><el-button v-if="canSchedule && !preRule" :disabled="!selectedDays.length" @click="adjustDialog=true">申请调整所选日期</el-button></div>
          <el-table :data="data.days" @selection-change="(rows:any[])=>selectedDays=rows" max-height="600" :empty-text="preRule ? '规则尚未配置，当前没有正式发布排班' : '该时段尚未发布排班，打卡按待确认记录'">
            <el-table-column v-if="canSchedule" type="selection" width="42"/><el-table-column prop="employee_name" label="人员" width="130"/><el-table-column prop="date" label="日期" width="120"/>
            <el-table-column label="原定安排" width="130"><template #default="{row}">{{labels[row.original_kind]}}</template></el-table-column>
            <el-table-column label="当前安排" width="170"><template #default="{row}">{{row.label}}<span v-if="row.rest_status" :class="restNeedsAction(row.rest_status)?'warning':'hint'"> · {{restStatusLabel(row.rest_status)}}</span></template></el-table-column><el-table-column label="班别" width="95"><template #default="{row}">{{shiftLabel(row.shift_label)}}</template></el-table-column><el-table-column prop="required_hours" label="应出勤工时" width="110"/><el-table-column prop="reason" label="调整原因"/>
          </el-table>
        </section>
        <section v-if="preRule" class="panel">
          <h2>已确认的待生效计划</h2>
          <p class="hint">可通过上方重新预览并再次确认来调整；保存现场轮休规则后，这些日期才正式发布并通知人员。规则保存前无法打卡。</p>
          <el-table :data="data.draft_days || []" max-height="460" empty-text="该周还没有已确认的待生效计划">
            <el-table-column prop="employee_name" label="人员" width="135"/><el-table-column prop="date" label="日期" width="120"/>
            <el-table-column label="原定" width="130"><template #default="{row}">{{labels[row.original_kind]}}</template></el-table-column>
            <el-table-column label="当前安排" width="130"><template #default="{row}">{{labels[row.kind]}}</template></el-table-column>
            <el-table-column prop="reason" label="原因"/><el-table-column label="状态" width="150"><template #default>已确认，待规则生效</template></el-table-column>
          </el-table>
        </section>
      </el-tab-pane>
      <el-tab-pane v-if="!preRule" label="申请与出勤确认" name="claims">
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
      <el-tab-pane v-if="!preRule" label="待休余额与流水" name="ledger"><section class="panel"><h2>可用待休</h2><p class="hint">每满 6 个有效工作日获得 1 天额度；上六休一的常规轮休在额度足够时自动扣减，延后或集中休息须由员工申请并经售后经理批准。这里单独记录现场轮休来源。</p><el-table :data="data.balances"><el-table-column label="人员"><template #default="{row}">{{row.employee_name || name(row.employee_id)}}</template></el-table-column><el-table-column prop="days" label="可用天数"/></el-table><h2>流水</h2><el-table :data="data.ledger"><el-table-column label="人员"><template #default="{row}">{{name(row.employee_id)}}</template></el-table-column><el-table-column prop="claim_id" label="申请号"/><el-table-column label="变动天数"><template #default="{row}">{{row.delta_units/2}}</template></el-table-column><el-table-column label="变动后余额"><template #default="{row}">{{row.balance_units/2}}</template></el-table-column><el-table-column prop="reason" label="原因"/><el-table-column prop="created_at" label="时间"/></el-table></section></el-tab-pane>
    </el-tabs>
    <el-dialog v-model="adjustDialog" title="申请调整已发布排班" width="500px"><p>提交后，项目组长、区域组长、售后经理依次审核；全部批准后才改变排班。原定轮休日及周期保留。</p><el-select v-model="adjustKind"><el-option label="上班" value="work"/><el-option label="正常轮休" value="rest"/></el-select><el-select v-if="adjustKind==='work'" v-model="adjustShiftLabel" placeholder="班别（仅展示）" style="margin-top:12px"><el-option label="保留原班别" value=""/><el-option label="不标注班别" value="none"/><el-option label="白班" value="day"/><el-option label="夜班" value="night"/></el-select><el-input v-model="adjustReason" type="textarea" placeholder="填写调整原因" maxlength="500"/><template #footer><el-button @click="adjustDialog=false">返回</el-button><el-button type="primary" :loading="saving" @click="adjust">提交调班申请</el-button></template></el-dialog>
    <el-dialog v-model="reviewDialog" title="处理轮休申请" width="520px"><p>{{reviewRow?.employee_name}} · {{claimLabels[reviewRow?.kind]}} · {{reviewRow?.days.map((d:any)=>d.date+' '+labels[d.kind]).join('、')}}</p><template v-if="reviewRow?.kind==='credit' && reviewAction==='approve'"><p>实际{{Number(reviewRow.current_facts?.hours||0).toFixed(2)}}小时 / 要求{{reviewRow.current_facts?.required_hours}}小时</p><p v-if="reviewRow.current_facts?.issues?.length" class="warning">打卡异常：{{reviewRow.current_facts.issues.join('、')}}</p><el-select v-model="reviewDays"><el-option v-for="n in [0,0.5,1]" :key="n" :label="`${n}天`" :value="n"/></el-select><p class="hint">不足半天时，填写理由后可确认零或半天。缺卡须先补齐。</p></template><el-input v-model="reviewNote" type="textarea" placeholder="填写审核或撤销原因" maxlength="500"/><template #footer><el-button @click="reviewDialog=false">返回</el-button><el-button type="primary" :loading="saving" @click="review">确认{{reviewAction==='approve'?'批准':reviewAction==='reject'?'驳回':'撤销'}}</el-button></template></el-dialog>
  </div>
</template>
<script setup lang="ts">
import {ref,computed,onMounted,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import {get,post,put} from '@/utils/request'
import {getWebAuthRoles,isWebAuthSuperuser} from '@/utils/authSession'
const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
function addDays(day:string,n:number){const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)}
function monday(day:string){const weekday=new Date(day+'T12:00:00Z').getUTCDay();return addDays(day,-((weekday+6)%7))}
const route=useRoute()
const router=useRouter()
const loading=ref(false),saving=ref(false),error=ref(''),tab=ref('calendar'),directory=ref<any>({projects:[],assignments:[],pending_members:[],people:[],rules:[],managed_project_ids:[]})
const projectId=ref<number>(),filterEmployee=ref<number>(),weekAnchor=ref(today)
const data=ref<any>({days:[],draft_days:[],claims:[],balances:[],ledger:[]}),selectedDays=ref<any[]>([])
const person=ref<any>({employee_id:undefined,plan_id:null,template:'six_one',start_date:today,end_date:addDays(today,6),cycle_start:today,reason:'周期排班',days:[]})
const personPreview=ref<any>({rows:[],revision:''}),savedPersonPlans=ref<any[]>([])
const personManualDates=new Set<string>()
const bindableDays=ref<any[]>([]),selectedBindableDates=ref<string[]>([])
const establishedCycleStart=ref<string|null>(null)
const entitlementPolicy=ref<any>({enabled:false,effective_date:null}),policyDate=ref(''),policyLoading=ref(false)
const isAdmin=computed(()=>isWebAuthSuperuser() || getWebAuthRoles().includes('admin'))
const canReviewEntitlement=computed(()=>isAdmin.value || getWebAuthRoles().includes('aftersales_manager'))
const pendingWorkDays=ref<any[]>([]),reviewingWorkDay=ref(false)
const weekStartDate=computed(()=>monday(weekAnchor.value))
const weekEndDate=computed(()=>addDays(weekStartDate.value,6))
const range=computed(()=>[weekStartDate.value,weekEndDate.value])
const projects=computed<any[]>(()=>directory.value.projects)
const selectedProject=computed(()=>projects.value.find((p:any)=>p.id===projectId.value))
const projectPendingMembers=computed<any[]>(()=>Array.isArray(directory.value.pending_members) ? directory.value.pending_members.filter((person:any)=>person.project_id===projectId.value) : [])
const preRule=computed(()=>!!selectedProject.value && !selectedProject.value.rule_id)
const draftMissingIds=computed(()=>new Set<number>(Array.isArray(selectedProject.value?.pre_rule_schedule_missing_member_ids) ? selectedProject.value.pre_rule_schedule_missing_member_ids.map(Number) : []))
const draftMissingNames=computed(()=>{
  const missing=projectPendingMembers.value.filter((person:any)=>draftMissingIds.value.has(Number(person.employee_id)))
  return missing.map((person:any)=>person.employee_name || name(person.employee_id)).join('、') || '组长和全部组员'
})
const canSchedule=computed(()=>!!(selectedProject.value?.can_schedule ?? (directory.value.can_manage || directory.value.schedule_project_ids?.includes(projectId.value))))
const canManage=canSchedule
const selectedRule=computed(()=>directory.value.rules.find((r:any)=>r.id===selectedProject.value?.rule_id))
const rotationRule=computed(()=>selectedRule.value?.rule_type==='rotation')
const memberAssignments=computed<any[]>(()=>directory.value.assignments.filter((a:any)=>a.project_id===projectId.value && (a.is_current || a.status==='active')))
const members=computed<any[]>(()=>{const ids=new Set(preRule.value ? projectPendingMembers.value.map((p:any)=>p.employee_id) : memberAssignments.value.map((a:any)=>a.employee_id));return directory.value.people.filter((p:any)=>ids.has(p.id))})
const eligibleMembers=computed<any[]>(()=>members.value)
const personRows=computed<any[]>(()=>personPreview.value.rows.length ? personPreview.value.rows : person.value.days)
const personPatternLength=computed(()=>patternLength(person.value.template))
const canPreviewPerson=computed(()=>!!projectId.value && !!person.value.employee_id && !person.value.readonly && !!person.value.start_date && !!person.value.end_date && !!person.value.cycle_start && person.value.cycle_start<=person.value.start_date && person.value.end_date>=addDays(person.value.start_date,personPatternLength.value-1) && person.value.start_date>=today && dayDifference(person.value.start_date,person.value.end_date)<366 && !!String(person.value.reason||'').trim() && (preRule.value || rotationRule.value))
const canConfirmPerson=computed(()=>canPreviewPerson.value && !!personPreview.value.revision && personPreview.value.rows.length>0)
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
  person.value.employee_id=eligibleMembers.value[0]?.id
  await personChanged()
  await reloadCalendar()
  if(isAdmin.value)await loadPolicy()
  if(canReviewEntitlement.value)await loadPendingWorkDays()
}catch(e){error.value=message(e)}finally{loading.value=false}}
function patternLength(template:string){return template==='twelve_two'?14:template==='eighteen_three'?21:7}
function templateLabel(template:string){return ({six_one:'上 6 休 1',twelve_two:'上 12 休 2',eighteen_three:'上 18 休 3',custom:'自定义'} as Record<string,string>)[template] || template}
function personStatusLabel(status:string){return ({pending_rule:'待规则生效',pending_project:'待分配项目',published:'已发布',preview:'待预览',ready:'待确认'} as Record<string,string>)[status] || status}
function restStatusLabel(status:string){return ({ready:'额度已具备',pending_credit:'待兑现',pending_application:'待申请调休',pending_review:'待售后经理审批',approved:'已批准',needs_action:'待处理'} as Record<string,string>)[status] || status}
function restNeedsAction(status:string){return ['pending_credit','pending_application','pending_review','needs_action'].includes(status)}
function dayDifference(start:string,end:string){return Math.round((new Date(end+'T12:00:00Z').getTime()-new Date(start+'T12:00:00Z').getTime())/86400000)}
function defaultDayKind(day:string){const length=patternLength(person.value.template);const position=((dayDifference(person.value.cycle_start,day)%length)+length)%length;return position<length-length/7?'work':'rest'}
function invalidatePersonPreview(){personPreview.value={rows:[],revision:''}}
function generatePersonDays(preserveManual=false){
  invalidatePersonPreview()
  const prior=new Map<string,any>(person.value.days.map((row:any)=>[row.date,row]))
  const rows:any[]=[]
  if(!person.value.start_date || !person.value.end_date || person.value.end_date<person.value.start_date)return
  for(let day=person.value.start_date;day<=person.value.end_date && rows.length<366;day=addDays(day,1))rows.push(preserveManual && personManualDates.has(day) && prior.has(day) ? {...prior.get(day)} : {date:day,kind:defaultDayKind(day),project_id:projectId.value ?? null,shift_label:null})
  person.value.days=rows
}
function newPersonPlan(){
  personManualDates.clear()
  const latest=savedPersonPlans.value.reduce((best:string,item:any)=>item.end_date>best?item.end_date:best,'')
  const start=[latest>=today?addDays(latest,1):today,String(selectedRule.value?.effective_date||'')].sort().pop() || today
  person.value={employee_id:person.value.employee_id,plan_id:null,readonly:false,template:'six_one',start_date:start,end_date:addDays(start,6),cycle_start:establishedCycleStart.value || start,reason:'周期排班',days:[]}
  generatePersonDays()
}
async function loadPersonPlans(){
  if(!projectId.value || !person.value.employee_id){savedPersonPlans.value=[];bindableDays.value=[];return}
  const response:any=await get(`/rotation/projects/${projectId.value}/person-plans/${person.value.employee_id}`)
  savedPersonPlans.value=Array.isArray(response?.plans)?response.plans:[]
  bindableDays.value=Array.isArray(response?.bindable_days)?response.bindable_days:[]
  establishedCycleStart.value=response?.established_cycle_start || null
  selectedBindableDates.value=[]
}
async function personChanged(){
  savedPersonPlans.value=[]
  bindableDays.value=[]
  establishedCycleStart.value=null
  if(person.value.employee_id)await loadPersonPlans()
  newPersonPlan()
}
function editSavedPlan(saved:any){
  person.value={employee_id:person.value.employee_id,plan_id:saved.id,readonly:saved.editable===false,template:saved.template,start_date:saved.start_date,end_date:saved.end_date,cycle_start:saved.cycle_start,reason:saved.reason || '周期排班',days:(saved.rows||[]).map((row:any)=>({date:row.date,kind:row.kind,project_id:row.project_id,shift_label:row.shift_label??null}))}
  personManualDates.clear();for(const row of person.value.days)personManualDates.add(row.date)
  invalidatePersonPreview()
}
function templateChanged(){personManualDates.clear();person.value.end_date=addDays(person.value.start_date,patternLength(person.value.template)-1);generatePersonDays()}
function personStartChanged(){personManualDates.clear();person.value.cycle_start=establishedCycleStart.value || person.value.start_date;person.value.end_date=addDays(person.value.start_date,patternLength(person.value.template)-1);generatePersonDays()}
function personRangeChanged(){generatePersonDays(true)}
function editPersonDay(date:string,field:'kind'|'project_id'|'shift_label',value:any){
  const row=person.value.days.find((item:any)=>item.date===date)
  if(!row)return
  row[field]=field==='project_id'?value==='null'?null:value:field==='shift_label'?(value||null):value
  personManualDates.add(date)
  if(field==='kind' && value==='rest')row.shift_label=null
  invalidatePersonPreview()
}
function personBody(){return {plan_id:person.value.plan_id,start_date:person.value.start_date,end_date:person.value.end_date,cycle_start:person.value.cycle_start,template:person.value.template,days:person.value.days.map((row:any)=>({date:row.date,kind:row.kind,project_id:row.project_id,shift_label:row.shift_label??null})),reason:person.value.reason}}
async function previewPerson(){if(!canPreviewPerson.value)return;await run(async()=>{personPreview.value=await post(`/rotation/projects/${projectId.value}/person-plans/${person.value.employee_id}/preview`,personBody())})}
async function confirmPerson(){
  if(!canConfirmPerson.value)return
  await ElMessageBox.confirm(preRule.value?'仅确认此人个人排班；建立项目规则前不能打卡或计算额度。':'确认后属于本项目的未来日期将发布给员工；已发布日期须通过调班审核修改。','确认个人排班')
  await run(async()=>{
    await put(`/rotation/projects/${projectId.value}/person-plans/${person.value.employee_id}/confirm?revision=${encodeURIComponent(personPreview.value.revision)}`,personBody())
    directory.value=await get('/field/directory')
    await loadPersonPlans()
    await reloadCalendar()
    invalidatePersonPreview()
    ElMessage.success('此人个人排班已确认')
  })
}
async function bindSelectedDays(){
  if(!projectId.value || !person.value.employee_id || !selectedBindableDates.value.length)return
  const grouped=new Map<number,string[]>()
  for(const token of selectedBindableDates.value){const [id,date]=token.split('|');const planId=Number(id);if(!grouped.has(planId))grouped.set(planId,[]);grouped.get(planId)!.push(date)}
  await run(async()=>{for(const [planId,dates] of grouped)await post(`/rotation/projects/${projectId.value}/person-plans/${person.value.employee_id}/bind`,{plan_id:planId,dates,reason:String(person.value.reason||'').trim()||'项目交接后接入'});await loadPersonPlans();await reloadCalendar();ElMessage.success('所选日期已接入本项目')})
}
async function projectChanged(){filterEmployee.value=undefined;person.value.employee_id=eligibleMembers.value[0]?.id;await personChanged();await reloadCalendar()}
async function changeWeek(offset:number){weekAnchor.value=addDays(weekStartDate.value,offset*7);await reloadCalendar()}
async function goThisWeek(){weekAnchor.value=today;await reloadCalendar()}
async function loadPolicy(){entitlementPolicy.value=await get('/rotation/entitlement/policy');policyDate.value=entitlementPolicy.value.effective_date || ''}
async function savePolicy(){if(!policyDate.value)return ElMessage.warning('请选择滚动额度生效日期');policyLoading.value=true;try{entitlementPolicy.value=await put('/rotation/entitlement/policy',{effective_date:policyDate.value});ElMessage.success('滚动额度生效日期已保存')}catch(e){error.value=message(e)}finally{policyLoading.value=false}}
async function loadPendingWorkDays(){pendingWorkDays.value=await get('/rotation/entitlement/pending')}
async function reviewWorkDay(row:any,action:'approve'|'reject'){
  let value=''
  try{const result=await ElMessageBox.prompt(`${row.employee_name} · ${row.date}，建议计 ${row.suggested_days} 天。请填写确认原因。`,action==='approve'?'确认有效工作日':'驳回有效工作日',{inputPattern:/\S+/,inputErrorMessage:'请填写原因'});value=result.value}catch{return}
  reviewingWorkDay.value=true
  try{await post(`/rotation/entitlement/days/${row.date}/review`,{employee_id:row.employee_id,action,work_days:action==='approve'?row.suggested_days:0,note:value});await loadPendingWorkDays();ElMessage.success('已处理')}catch(e){error.value=message(e)}finally{reviewingWorkDay.value=false}
}
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
.policy-controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bindable-days{padding:12px 0}.bindable-days h3{font-size:15px}.bindable-days .el-checkbox-group{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
</style>
