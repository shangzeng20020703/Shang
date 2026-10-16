<template>
  <ion-page><ion-content><div class="service-mobile rotation-mobile">
    <header><button @click="$router.push('/app/attendance')">返回打卡</button><h1>我的排班与待休</h1><p>正常轮休不扣余额，额外调休需审批。</p></header>
    <p v-if="error" role="alert" class="service-error">{{error}}</p>
    <label class="service-field">查看月份<input v-model="month" type="month" @change="load"/></label>
    <label class="service-field">项目<select v-model.number="projectId" @change="load"><option :value="0">全部个人安排</option><option v-for="p in directory.projects" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
    <label v-if="canManage" class="service-row"><span>查看本项目待审申请</span><input type="checkbox" v-model="manageView" @change="load"/></label>
    <section class="service-card"><h2>待休余额</h2><p v-for="b in data.balances" :key="b.employee_id">{{b.employee_name || '本人'}}：<strong>{{b.days}}天</strong></p><p v-if="!data.balances.length">0天</p></section>
    <section class="service-card"><h2>个人排班日历</h2><p class="muted">未发布日期为待确认，打卡仍可留存。半天调休当天工时减半。</p><div class="calendar-grid"><span v-for="w in ['一','二','三','四','五','六','日']" :key="w" class="weekday">{{w}}</span><span v-for="i in monthOffset" :key="`blank-${i}`"></span><div v-for="d in calendarDays" :key="d.date" :class="['day',d.kind,{today:d.date===today}]"><b>{{Number(d.date.slice(-2))}}</b><small>{{d.label}}</small><small v-if="d.required_hours">{{d.required_hours}}h</small></div></div></section>
    <section class="service-card"><h2>提交申请</h2><label class="service-field">申请项目<select v-model.number="form.project_id"><option :value="0" disabled>选择项目</option><option v-for="p in directory.projects" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
      <label class="service-field">申请类型<select v-model="form.kind" @change="form.dayKind=form.kind==='leave'?'leave':'work'"><option value="schedule">调班 / 休息日出勤安排</option><option value="credit">休息日出勤事后确认</option><option value="leave">使用待休余额</option></select></label>
      <label class="service-field">开始日期<input v-model="form.start" type="date"/></label><label class="service-field">结束日期<input v-model="form.end" type="date"/></label>
      <label class="service-field">安排<select v-model="form.dayKind"><template v-if="form.kind==='leave'"><option value="leave">全天调休</option><option value="half_leave">半天调休</option></template><template v-else><option value="work">上班 / 出勤</option><option v-if="form.kind==='schedule'" value="rest">正常轮休</option></template></select></label>
      <label class="service-field">原因<textarea v-model="form.reason" maxlength="500" placeholder="说明现场安排或事后确认原因"></textarea></label><button class="service-primary" :disabled="saving||!form.project_id" @click="submit">提交给项目负责人</button>
    </section>
    <section class="service-card"><h2>{{manageView?'本项目申请':'我的申请'}}</h2><article v-for="c in data.claims" :key="c.id" class="claim"><strong>{{c.employee_name}} · {{claimLabels[c.kind]}} · {{statusLabels[c.status]}}</strong><p>{{c.days.map((d:any)=>d.date).join('、')}}</p><p>{{c.reason}}</p><p v-if="c.current_facts">实际{{Number(c.current_facts.hours).toFixed(2)}} / {{c.current_facts.required_hours}}小时，建议{{c.current_facts.suggested_days}}天</p><p v-if="c.needs_review" class="service-error">打卡已变化，原确认额度需复核。</p><p v-if="c.review_note">审核：{{c.review_note}} · {{c.days_amount}}天</p><div v-if="manageView&&canManage"><button v-if="c.status==='pending'" @click="openReview(c,'approve')">批准</button> <button v-if="c.status==='pending'" @click="openReview(c,'reject')">驳回</button><button v-if="c.status==='approved'" @click="openReview(c,'revoke')">撤销</button></div></article><p v-if="!data.claims.length" class="muted">暂无申请</p></section>
    <section class="service-card"><h2>待休流水</h2><p v-for="l in data.ledger" :key="l.id">申请{{l.claim_id}}：{{l.delta_units>0?'+':''}}{{l.delta_units/2}}天，余额{{l.balance_units/2}}天<br/><small>{{l.reason}}</small></p></section>
    <section v-if="reviewRow" class="service-card review"><h2>处理申请</h2><label v-if="reviewRow.kind==='credit'&&reviewAction==='approve'" class="service-field">确认天数<select v-model.number="reviewDays"><option :value="0">0天</option><option :value="0.5">0.5天</option><option :value="1">1天</option></select></label><label class="service-field">审核原因<textarea v-model="reviewNote" maxlength="500"></textarea></label><button class="service-primary" :disabled="saving" @click="review">确认{{reviewAction==='approve'?'批准':reviewAction==='reject'?'驳回':'撤销'}}</button><button @click="reviewRow=null">返回</button></section>
  </div></ion-content></ion-page>
</template>
<script setup lang="ts">
import {ref,computed} from 'vue'
import {IonPage,IonContent,onIonViewWillEnter} from '@ionic/vue'
import {get,post} from '@/utils/request'
import {useAuthStore} from '@/stores/auth'
const auth=useAuthStore(),directory=ref<any>({projects:[],managed_project_ids:[],can_manage:false}),data=ref<any>({days:[],claims:[],balances:[],ledger:[]}),error=ref(''),saving=ref(false)
const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()),month=ref(today.slice(0,7)),projectId=ref(0),manageView=ref(false)
const canManage=computed(()=>projectId.value>0&&(directory.value.can_manage||directory.value.managed_project_ids.includes(projectId.value)))
const labels:any={work:'上班',rest:'轮休',leave:'调休',half_leave:'半天调休',pending:'待确认'},claimLabels:any={schedule:'调班',credit:'出勤确认',leave:'调休'},statusLabels:any={pending:'待审批',approved:'已批准',rejected:'已驳回',revoked:'已撤销'}
function addDays(day:string,n:number){const d=new Date(day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10)}
const start=computed(()=>month.value+'-01'),end=computed(()=>{const d=new Date(start.value+'T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+1);d.setUTCDate(0);return d.toISOString().slice(0,10)})
const monthOffset=computed(()=>(new Date(start.value+'T12:00:00Z').getUTCDay()+6)%7)
const calendarDays=computed(()=>{const rows=[];for(let d=start.value;d<=end.value;d=addDays(d,1)){const row=data.value.days.find((r:any)=>r.employee_id===auth.user?.id&&r.date===d);rows.push(row||{date:d,kind:'pending',label:labels.pending})}return rows})
async function load(){try{directory.value=await get('/field/directory');if(!canManage.value)manageView.value=false;data.value=await get('/rotation/calendar',{start:start.value,end:end.value,project_id:projectId.value||undefined,employee_id:manageView.value?undefined:auth.user?.id})}catch(e){error.value=message(e)}}
function message(e:any){return typeof e.response?.data?.detail==='string'?e.response.data.detail:'操作失败，请核对日期和原因'}
const form=ref({project_id:0,kind:'schedule',dayKind:'work',start:today,end:today,reason:''})
async function submit(){if(!form.value.reason.trim()){error.value='请填写原因';return}if(form.value.end<form.value.start||form.value.end>addDays(form.value.start,61)){error.value='每次申请须为1至62天';return}saving.value=true;error.value='';try{const days=[];for(let d=form.value.start;d<=form.value.end;d=addDays(d,1))days.push({date:d,kind:form.value.dayKind});await post('/rotation/claims',{project_id:form.value.project_id,kind:form.value.kind,days,reason:form.value.reason});form.value.reason='';await load()}catch(e){error.value=message(e)}finally{saving.value=false}}
const reviewRow=ref<any>(null),reviewAction=ref('approve'),reviewDays=ref(0),reviewNote=ref('')
function openReview(row:any,action:string){reviewRow.value=row;reviewAction.value=action;reviewDays.value=row.current_facts?.suggested_days||0;reviewNote.value=''}
async function review(){if(!reviewNote.value.trim()){error.value='请填写审核原因';return}saving.value=true;try{await post(`/rotation/claims/${reviewRow.value.id}/review`,{action:reviewAction.value,note:reviewNote.value,...(reviewRow.value.kind==='credit'?{days:reviewDays.value}:{})});reviewRow.value=null;await load()}catch(e){error.value=message(e)}finally{saving.value=false}}
onIonViewWillEnter(load)
</script>
<style scoped>
.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:5px}.day{border-radius:8px;min-height:69px;background:#f3f5f8;padding:6px 2px;text-align:center}.day small{display:block;font-size:10px;line-height:1.5}.work{background:#eaf5ff}.rest{background:#e7f5ee}.leave,.half_leave{background:#fff0d9}.today{outline:2px solid #397acf}.weekday{text-align:center;color:#63748a}.claim{padding:16px 0;border-bottom:1px solid #e5eaf0}.review{border:2px solid #397acf}.rotation-mobile h2{font-size:18px}
</style>
