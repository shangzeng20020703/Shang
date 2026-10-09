<template>
  <div class="dashboard" v-loading="loading">
    <header class="dashboard-header">
      <h1>项目人员看板</h1>
      <div class="dashboard-actions"><span>{{ day }}</span><el-button :disabled="loading" @click="load">刷新</el-button></div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <template v-if="data">
      <div class="metrics">
        <article v-for="metric in metrics" :key="metric.label" class="metric">
          <div class="metric-label">{{ metric.label }}</div>
          <div class="metric-number">{{ metric.value }}<span>{{ metric.unit }}</span></div>
        </article>
      </div>
      <section class="chart-card">
        <header class="chart-heading"><h2>当日项目人员安排</h2><el-input v-model="query" clearable aria-label="筛选项目" :prefix-icon="Search" /></header>
        <div v-if="projects.length" class="project-chart" role="list" aria-label="各项目安排人数">
          <el-tooltip v-for="project in projects" :key="project.id" placement="top" effect="light" :show-after="100">
            <template #content><div class="member-tooltip"><strong>{{ project.name }} · {{ project.people }} 人</strong><div class="member-names"><span v-for="member in project.members" :key="member.id">{{ member.name }}</span><span v-if="!project.people">暂无安排人员</span></div></div></template>
            <div class="project-row" role="listitem" tabindex="0" :aria-label="`${project.name}：${project.people} 人，${project.members.map(m=>m.name).join('、')||'暂无安排'}`">
              <span class="project-name">{{ project.name }}</span>
              <div class="bar-track"><div class="bar-fill" :style="{width:`${project.people/maxPeople*100}%`}" /></div>
              <strong class="project-count">{{ project.people }}<small>人</small></strong>
            </div>
          </el-tooltip>
        </div>
        <el-empty v-else description="暂无项目" :image-size="80" />
      </section>
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { get } from '@/utils/request'
type Project = {id:number;name:string;people:number;members:{id:number;name:string}[]}
type Dashboard = {people_total:number;assigned_people:number;staffing:Project[]}
const day=ref(''),data=ref<Dashboard|null>(null),loading=ref(false),error=ref(''),query=ref('')
const projects=computed(()=>data.value?.staffing.filter(p=>p.name.includes(query.value.trim()))||[])
const maxPeople=computed(()=>Math.max(1,...projects.value.map(p=>p.people)))
const metrics=computed(()=>data.value?[
 {label:'总人员数',value:data.value.people_total,unit:'人'},
 {label:'当日已安排人员',value:data.value.assigned_people,unit:'人'},
 {label:'已安排项目',value:data.value.staffing.filter(p=>p.people>0).length,unit:'个'},
]:[])
async function load(){
 if(loading.value)return
 loading.value=true;error.value='';day.value=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'})
 try{data.value=await get<Dashboard>('/field/dashboard',{day:day.value})}
 catch{data.value=null;error.value='看板加载失败，请刷新重试'}
 finally{loading.value=false}
}
onMounted(load)
</script>
<style scoped>
.dashboard{max-width:1600px;margin:auto;color:#24324a;min-height:360px}
.dashboard-header,.dashboard-actions,.chart-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}
.dashboard-header{margin-bottom:24px}h1{font-size:24px;margin:0}h2{font-size:16px;margin:0}.dashboard-actions{font-size:13px;color:#78869a}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin-bottom:24px}.metric,.chart-card{background:white;border:1px solid #e7ecf3;border-radius:12px}.metric{padding:24px}.metric-label{font-size:13px;color:#78869a}.metric-number{font-size:36px;font-weight:600;margin-top:14px;font-variant-numeric:tabular-nums}.metric-number span{font-size:12px;font-weight:400;color:#78869a;margin-left:10px}
.chart-card{padding:24px}.chart-heading{margin-bottom:24px}.chart-heading .el-input{max-width:260px}.project-chart{max-height:65vh;overflow:auto;padding:4px}.project-row{display:grid;grid-template-columns:minmax(130px,25%) 1fr 64px;gap:24px;align-items:center;padding:18px 12px;border-radius:8px}.project-row:hover,.project-row:focus-visible{background:#f6f7fc;outline:2px solid #e9eafb}.project-name{font-size:13px;line-height:1.5;overflow-wrap:anywhere}.bar-track{height:20px;background:#f1f3f9;border-radius:5px;overflow:hidden}.bar-fill{height:100%;background:linear-gradient(90deg,#7b80e4,#5363cf);border-radius:5px}.project-count{text-align:right;font-size:17px;font-variant-numeric:tabular-nums}.project-count small{font-size:11px;font-weight:400;color:#78869a;margin-left:7px}.member-tooltip{max-width:min(420px,80vw);font-size:13px}.member-names{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:12px;max-height:260px;overflow:auto;font-size:12px}
@media(max-width:650px){.metrics{gap:10px}.metric{padding:16px 12px}.metric-number{font-size:28px}.dashboard-header{flex-wrap:wrap}.chart-card{padding:16px}.chart-heading{flex-wrap:wrap}.project-row{grid-template-columns:minmax(80px,30%) 1fr 44px;gap:12px;padding:15px 4px}}
</style>
