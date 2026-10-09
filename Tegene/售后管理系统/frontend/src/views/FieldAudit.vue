<template>
  <div>
    <div class="page-intro"><div><h1>操作日志</h1><p>按业务模块查看操作内容及修改前后对照。</p></div><el-button @click="load">刷新</el-button></div>
    <section class="surface">
      <div class="field-toolbar"><el-select v-model="module" clearable placeholder="全部模块" style="width:220px" @change="reset"><el-option v-for="m in modules" :key="m.value" :value="m.value" :label="m.label"/></el-select><span>第 {{page}} 页</span></div>
      <el-table :data="rows" v-loading="loading" class="field-table">
        <el-table-column prop="operator_name" label="操作人" min-width="100"/>
        <el-table-column label="模块" min-width="110"><template #default="{row}">{{moduleLabel(row)}}</template></el-table-column>
        <el-table-column label="操作" min-width="120"><template #default="{row}">{{actionLabel(row)}}</template></el-table-column>
        <el-table-column label="操作对象" min-width="160"><template #default="{row}">{{resourceLabel(row)}}{{row.resource_id ? `（${row.resource_id}）` : ''}}</template></el-table-column>
        <el-table-column label="修改项 / 记录内容" min-width="240" show-overflow-tooltip><template #default="{row}">{{summary(row)}}</template></el-table-column>
        <el-table-column label="时间" min-width="175"><template #default="{row}">{{time(row.created_at)}}</template></el-table-column>
        <el-table-column label="详情" width="80"><template #default="{row}"><el-button link type="primary" @click="selected=row;visible=true">查看</el-button></template></el-table-column>
      </el-table>
      <div style="text-align:right;margin-top:20px"><el-button :disabled="page===1" @click="page--;load()">上一页</el-button><el-button :disabled="rows.length<50" @click="page++;load()">下一页</el-button></div>
    </section>
    <el-dialog v-model="visible" title="操作详情" width="min(860px, 94vw)">
      <template v-if="selected">
        <p>{{selected.operator_name || '系统'}} · {{moduleLabel(selected)}} · {{actionLabel(selected)}} · {{resourceLabel(selected)}} · {{time(selected.created_at)}}</p>
        <p v-if="!selected.before_data || selected.before_data==='null'" class="audit-note">此记录未保存修改前内容，以下仅展示已记录的数据。</p>
        <el-table :data="changes(selected)" max-height="480" empty-text="此记录没有具体字段变化">
          <el-table-column prop="field" label="修改项 / 记录项" width="170"/>
          <el-table-column prop="before" label="修改前" min-width="220"/>
          <el-table-column prop="after" label="修改后 / 记录值" min-width="220"/>
        </el-table>
      </template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {get} from '@/utils/request'
import {projectResources,peopleResources,moduleLabel,actionLabel,resourceLabel,changes,summary} from '@/utils/auditDisplay'
const rows=ref<any[]>([]),module=ref(''),page=ref(1),loading=ref(false),selected=ref<any>(null),visible=ref(false)
const modules=[{value:'organization',label:'组织架构',module:'organization'},{value:'project',label:'项目库',module:'field',resources:projectResources},{value:'people',label:'人员花名册',module:'field',resources:peopleResources},{value:'attendance',label:'考勤管理',module:'attendance'},{value:'leave',label:'考勤管理 · 假期余额',module:'leave'},{value:'approval',label:'审批管理',module:'approval'},{value:'system',label:'系统设置',module:'system'}]
async function load(){const filter=modules.find(m=>m.value===module.value);loading.value=true;try{rows.value=await get('/audit-logs',{module:filter?.module,resource_types:filter?.resources?.join(','),skip:(page.value-1)*50,limit:50})}finally{loading.value=false}}
function reset(){page.value=1;load()}
function time(v:string){return v?new Date(v.endsWith('Z')||/[+-]\d\d:\d\d$/.test(v)?v:v+'Z').toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'—'}
onMounted(load)
</script>
<style scoped>.audit-note{color:var(--el-text-color-secondary)}:deep(.el-dialog .cell){white-space:pre-wrap;overflow-wrap:anywhere}</style>
