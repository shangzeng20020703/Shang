<template>
  <ion-page><ion-content><div class="service-mobile">
    <header><span class="service-eyebrow">PROJECTS</span><h1>{{canSeeAllProjects?'项目库':'我的项目'}}</h1><p>{{canSeeAllProjects?'查看全部项目；仅修改被分配的项目。':'查看当前项目、参与记录和打卡规则。'}}</p></header>
    <p v-if="error" role="alert" class="service-error">{{error}}</p>
    <label class="service-field">查找项目<input v-model.trim="query" type="search" placeholder="项目名称或编号"/></label>
    <label class="service-row"><span>显示历史项目</span><input v-model="showHistory" type="checkbox"/></label>
    <section v-for="p in projects" :key="p.id" class="service-card">
      <small class="muted">{{p.code}}</small><h2>{{p.name}}</h2>
      <p>项目组长：{{p.manager_name || '待指定'}}</p><p>打卡规则：{{p.rule_name || '待区域组长配置'}}</p>
      <p v-for="a in pendingMembers(p.id)" :key="`pending-${a.id}`" class="muted">{{a.employee_name}} · 待个人排班及规则生效</p>
      <p v-for="a in ownMembers(p.id)" :key="a.id" class="muted">参与时间：{{formatTime(a.started_at)}} — {{a.ended_at?formatTime(a.ended_at):'至今'}} · {{state(a)}}</p>
      <p v-if="ownPendingMembers(p.id).length" class="muted">已列入项目待配置名单；个人排班和项目规则生效后方可按本项目打卡。</p>
      <ion-button v-if="canPunch && ownMembers(p.id).some((a:any)=>a.is_current && a.can_punch!==false)" expand="block" router-link="/app/attendance">去打卡</ion-button>
      <ion-button v-if="p.can_schedule || canSeeAllProjects" fill="outline" expand="block" :router-link="`/app/rotation?project_id=${p.id}`">查看排班与申请</ion-button>
      <ion-button v-if="p.manager_id===auth.user?.id" fill="outline" expand="block" router-link="/app/team-attendance">查看项目人员考勤</ion-button>
      <ion-button v-if="canManage(p)" fill="outline" expand="block" @click="open(p)">{{selected===p.id?'收起配置':'配置人员与规则'}}</ion-button>
      <template v-if="selected===p.id && canManage(p)">
        <h3>1. 确认项目组长和组员</h3>
        <label class="service-field">项目组长<select v-model.number="managerId" :disabled="saving"><option :value="0">待指定</option><option v-for="person in leaderCandidates" :key="person.id" :value="person.id">{{person.name}} · {{person.employee_no}}</option></select></label>
        <template v-if="!p.rule_id">
          <div class="service-field"><span>项目组员（多选）</span><div class="member-checks"><label v-for="person in memberCandidates" :key="person.id"><input v-model="form.employee_ids" type="checkbox" :value="person.id" :disabled="saving || (hasCurrentProject(person.id) && !pendingMembers(p.id).some((row:any)=>row.employee_id===person.id))"/>{{person.name}} · {{person.employee_no}}</label></div></div>
          <button class="service-primary" :disabled="saving || !managerId || !form.employee_ids.length" @click="confirmRoster(p.id)">确认人员配置</button>
          <p class="muted">组长自动加入名单；确认后逐人安排完整周期，再设置规则。</p>
        </template>
        <template v-else><button class="service-primary" :disabled="saving || managerId===Number(p.manager_id||0)" @click="saveLeader(p.id)">保存项目组长</button><label class="service-field">添加组员<select v-model.number="form.employee_id" :disabled="saving"><option :value="0" disabled>仅显示组员</option><option v-for="person in memberCandidates" :key="person.id" :value="person.id" :disabled="hasCurrentProject(person.id)">{{person.name}} · {{person.employee_no}}</option></select></label><button class="service-primary" :disabled="saving || !form.employee_id" @click="saveMembers(p.id)">添加到项目</button></template>
        <h3>待配置人员</h3>
        <div v-for="a in pendingMembers(p.id)" :key="a.id" class="project-member"><div class="service-row"><strong>{{a.employee_name}}</strong><span class="service-chip">{{a.is_leader_auto?'项目组长':'待配置'}}</span></div><ion-button v-if="!a.is_leader_auto" fill="clear" color="danger" size="small" :disabled="saving" @click="removePending(p.id,a.id)">移出名单</ion-button></div>
        <p v-if="!pendingMembers(p.id).length" class="muted">暂无待配置人员</p>
        <h3>2. 逐人确认个人排班</h3>
        <p class="muted">每人独立选择开始日期、周期及每天安排；未来项目未确定的日期可留为待分配。</p>
        <ion-button expand="block" :router-link="`/app/rotation?project_id=${p.id}`">打开本项目个人排班</ion-button>
        <p v-if="!p.rule_id" class="muted">已确认 {{p.pre_rule_schedule_member_count || 0}} / {{pendingMembers(p.id).length}} 人的完整周期。</p>
        <h3>3. 关联现场轮休规则</h3>
        <label class="service-field">项目打卡规则<select v-model.number="ruleId" :disabled="saving"><option :value="0" disabled>请选择规则</option><option v-for="r in data.rules.filter((r:any)=>r.is_active)" :key="r.id" :value="r.id">{{r.name}}</option></select></label>
        <p class="muted">已打卡的分段保留原规则，新的项目分段使用当前规则。</p>
        <button class="service-primary" :disabled="saving || !ruleId || (!p.rule_id && !p.pre_rule_schedule_ready)" @click="saveRule(p.id)">保存规则并启用整份名单</button>
        <button v-if="p.rule_id && pendingMembers(p.id).length" class="service-primary" :disabled="saving" @click="activateMembers(p.id)">重试启用整份名单</button>
        <p v-if="!p.rule_id && !p.pre_rule_schedule_ready" class="muted">请先逐人确认完整周期，才能建立现场轮休规则。</p>
        <h3>参与人员</h3>
        <div v-for="a in members(p.id)" :key="a.id" class="project-member">
          <div class="service-row"><strong>{{a.employee_name}}</strong><span class="service-chip">{{state(a)}}</span></div>
          <p class="muted">{{formatTime(a.started_at)}} — {{a.ended_at?formatTime(a.ended_at):'至今'}}</p>
          <ion-button v-if="a.is_current" fill="clear" color="danger" size="small" :disabled="saving" @click="cancel(a.id)">移出项目</ion-button>
        </div>
        <p v-if="!members(p.id).length" class="muted">尚未添加人员</p>
      </template>
    </section>
    <p v-if="!projects.length" class="muted">暂无项目，请联系项目负责人分配。</p>
  </div></ion-content></ion-page>
</template>
<script setup lang="ts">
import {ref,computed} from 'vue'
import {IonPage,IonContent,IonButton,onIonViewWillEnter,alertController} from '@ionic/vue'
import {get,post,put,del} from '@/utils/request'
import {useAuthStore} from '@/stores/auth'
const auth=useAuthStore()
const canPunch=computed(()=>!auth.roles.includes('regional_lead')&&!auth.roles.includes('aftersales_manager'))
const canSeeAllProjects=computed(()=>auth.roles.includes('regional_lead')||auth.roles.includes('aftersales_manager'))
const data=ref<any>({assignments:[],pending_members:[],projects:[],people:[],rules:[],managed_project_ids:[]}),error=ref(''),saving=ref(false),query=ref(''),selected=ref(0),ruleId=ref(0),managerId=ref(0),showHistory=ref(false)
const memberCandidates=computed(()=>data.value.people.filter((p:any)=>p.is_active!==false && p.role==='employee' && p.field_staff_category==='member'))
const leaderCandidates=computed(()=>data.value.people.filter((p:any)=>p.is_active!==false && p.role==='employee' && (p.field_staff_category==='leader_candidate' || p.id===managerId.value)))
const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
const form=ref({employee_id:0,employee_ids:[] as number[]})
function canManage(p:any){return p.is_active && (p.can_manage ?? (data.value.can_manage || data.value.managed_project_ids.includes(p.id)))}
function members(id:number){return data.value.assignments.filter((a:any)=>a.project_id===id && (showHistory.value || (a.is_current)))}
function pendingMembers(id:number){return (data.value.pending_members||[]).filter((a:any)=>a.project_id===id)}
function ownPendingMembers(id:number){return pendingMembers(id).filter((a:any)=>a.employee_id===auth.user?.id)}
function ownMembers(id:number){return members(id).filter((a:any)=>a.employee_id===auth.user?.id)}
function hasCurrentProject(employeeId:number){return data.value.assignments.some((a:any)=>a.employee_id===employeeId && a.is_current) || (data.value.pending_members||[]).some((a:any)=>a.employee_id===employeeId)}
const projects=computed(()=>data.value.projects.filter((p:any)=>(auth.roles.includes('regional_lead')||auth.roles.includes('aftersales_manager')||canManage(p)||ownMembers(p.id).length||ownPendingMembers(p.id).length) && [p.name,p.code].join(' ').toLowerCase().includes(query.value.toLowerCase())))
function state(a:any){return a.is_current?'参与中':'已结束'}
function formatTime(v:string){return v?new Date(/[Zz]|[+-]\d\d:\d\d$/.test(v)?v:v+'Z').toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—'}
function open(p:any){selected.value=selected.value===p.id?0:p.id;ruleId.value=p.rule_id || 0;managerId.value=p.manager_id||0;error.value='';form.value={employee_id:0,employee_ids:pendingMembers(p.id).filter((row:any)=>!row.is_leader_auto).map((row:any)=>Number(row.employee_id))}}
async function load(){try{data.value=await get('/field/directory');if(form.value.employee_id && !memberCandidates.value.some((p:any)=>p.id===form.value.employee_id))form.value.employee_id=0}catch{error.value='加载失败，请重试'}}
function message(e:any){const detail=e.response?.data?.detail;if(typeof detail==='string')return detail;if(detail?.conflicts?.length)return `${detail.message||'人员未启用'}：${detail.conflicts.map((c:any)=>`${c.employee_name||c.employee_id}（${c.reason||c.project_name||'日期冲突'}）`).join('、')}`;return '操作失败，请检查人员、规则和日期'}
async function saveLeader(id:number){saving.value=true;error.value='';try{await put(`/field/projects/${id}/configuration`,{manager_id:managerId.value||null});await load()}catch(e){error.value=message(e)}finally{saving.value=false}}
async function confirmRoster(id:number){saving.value=true;error.value='';try{await put(`/field/projects/${id}/pending-roster`,{manager_id:managerId.value,member_ids:[...new Set(form.value.employee_ids)]});await load()}catch(e){error.value=message(e)}finally{saving.value=false}}
async function saveRule(id:number){saving.value=true;error.value='';try{await put(`/field/projects/${id}/configuration`,{rule_id:ruleId.value});await load();if(pendingMembers(id).length)await post(`/field/projects/${id}/members/activate`);await load()}catch(e){error.value=message(e);await load()}finally{saving.value=false}}
async function activateMembers(id:number){saving.value=true;error.value='';try{await post(`/field/projects/${id}/members/activate`);await load()}catch(e){error.value=message(e)}finally{saving.value=false}}
async function saveMembers(id:number){saving.value=true;error.value='';try{await post(`/field/projects/${id}/members`,{employee_ids:[form.value.employee_id]});form.value.employee_id=0;await load()}catch(e){error.value=message(e)}finally{saving.value=false}}
async function removePending(projectId:number,pendingId:number){saving.value=true;error.value='';try{await del(`/field/projects/${projectId}/pending-members/${pendingId}`);await load()}catch(e){error.value=message(e)}finally{saving.value=false}}
async function cancel(id:number){const dialog=await alertController.create({header:'移出项目',message:'移出立即生效，历史记录保留，未完成的考勤待核对。',buttons:[{text:'返回',role:'cancel'},{text:'确认取消',handler:async()=>{saving.value=true;error.value='';try{await post(`/field/assignments/${id}/remove`);await load()}catch(e){error.value=message(e)}finally{saving.value=false}}}]});await dialog.present()}
onIonViewWillEnter(load)
</script>
<style scoped>
.service-card h2{font-size:20px;line-height:1.5;margin:6px 0 12px}.project-member{border-top:1px solid #e6edf0;padding:14px 0 0}.service-card h3{font-size:16px;margin-top:24px}
.member-checks{display:flex;flex-wrap:wrap;gap:8px}.member-checks label{display:inline-flex;align-items:center;gap:5px;padding:8px;border:1px solid #dce4ee;border-radius:8px}
</style>
