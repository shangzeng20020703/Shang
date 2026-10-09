<template>
  <div class="org-page" v-loading="loading">
    <div class="page-intro"><div><h1>组织架构</h1><p>部门内部最多四级，岗位与人员花名册联动。</p></div><div><el-button @click="load">刷新</el-button><el-button type="primary" @click="editDepartment()">新增部门</el-button></div></div>
    <section class="surface">
      <el-tabs v-model="tab">
        <el-tab-pane label="架构图" name="chart">
          <div class="org-chart"><OrgTreeNode v-for="root in tree" :key="root.id" :node="root" :is-root="true" :collapsed="collapsed" @toggle="toggle" @edit="editDepartment" @add="addChild" @remove="removeDepartment" @view-members="viewDepartment"/><el-empty v-if="!tree.length" description="暂无部门，请先新增部门"/></div>
        </el-tab-pane>
        <el-tab-pane label="部门岗位" name="positions">
          <div class="position-toolbar"><el-select v-model="selectedDepartment" clearable placeholder="全部部门" aria-label="筛选部门" style="width:260px"><el-option v-for="d in departments" :key="d.id" :label="departmentPath(d.id)" :value="d.id"/></el-select><div><el-button @click="positionsVisible=true">查看岗位</el-button><el-button type="primary" :disabled="!departments.length" @click="positionForm={department_id:selectedDepartment,name:''};positionVisible=true">新增岗位</el-button></div></div>
          <div class="stats">
            <div><span>岗位总数</span><strong>{{filteredPositions.length}}</strong><small>按部门分别统计</small></div>
            <div><span>在岗人数</span><strong>{{onboard}}</strong><small>来自花名册中的在职、试用人员</small></div>
            <div><span>空岗数量</span><strong>{{filteredPositions.filter(p=>!p.member_count).length}}</strong><small>已设置但暂无人员的岗位</small></div>
            <div><span>平均岗位人数</span><strong>{{filteredPositions.length?(onboard/filteredPositions.length).toFixed(2):'0.00'}}</strong><small>在岗人数 ÷ 岗位数</small></div>
          </div>

        </el-tab-pane>
      </el-tabs>
    </section>
    <el-dialog v-model="positionsVisible" title="部门岗位" width="min(900px, 94vw)"><div class="position-list"><div v-for="p in filteredPositions" :key="`${p.department_id}:${p.name}`" class="position-card"><span class="department-label">{{p.department_id?departmentPath(p.department_id):p.department_name}}</span><h3>{{p.name}} <el-tag size="small">{{p.member_count}} 人</el-tag></h3><p>{{p.members.map((m:any)=>m.name).join('、') || '暂未安排人员'}}</p><el-button link type="primary" @click="router.push('/employees')">到花名册安排人员</el-button></div><el-empty v-if="!filteredPositions.length" description="暂无岗位，点击上方新增即可"/></div></el-dialog>
    <el-dialog v-model="departmentVisible" :title="departmentForm.id?'编辑部门':'新增部门'" width="460px">
      <el-form label-position="top"><el-form-item label="部门名称" required><el-input v-model="departmentForm.name" maxlength="100"/></el-form-item><el-form-item label="上级部门"><el-select v-model="departmentForm.parent_id" clearable placeholder="无上级（一级部门）" style="width:100%"><el-option v-for="d in parentOptions" :key="d.id" :value="d.id" :label="departmentPath(d.id)"/></el-select></el-form-item><el-form-item label="部门负责人"><el-select v-model="departmentForm.manager_id" clearable filterable style="width:100%"><el-option v-for="p in people.filter(p=>p.is_active && ['在职','试用'].includes(p.status))" :key="p.id" :label="p.name" :value="p.id"/></el-select></el-form-item></el-form>
      <template #footer><el-button @click="departmentVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveDepartment">保存</el-button></template>
    </el-dialog>
    <el-dialog v-model="positionVisible" title="新增部门岗位" width="460px">
      <el-form label-position="top"><el-form-item label="所属部门" required><el-select v-model="positionForm.department_id" style="width:100%"><el-option v-for="d in departments" :key="d.id" :label="departmentPath(d.id)" :value="d.id"/></el-select></el-form-item><el-form-item label="岗位名称" required><el-input v-model="positionForm.name" maxlength="100" placeholder="例如：售后工程师、区域组长" @keyup.enter="savePosition"/></el-form-item></el-form>
      <template #footer><el-button @click="positionVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="savePosition">新增</el-button></template>
    </el-dialog>
    <el-dialog v-model="membersVisible" title="部门成员" width="600px"><el-table :data="members"><el-table-column prop="name" label="姓名"/><el-table-column prop="position" label="岗位"/><el-table-column prop="status" label="在职状态"/></el-table><el-button link type="primary" @click="router.push('/employees')">前往人员花名册</el-button></el-dialog>
  </div>
</template>
<script setup lang="ts">
import {ref,computed,onMounted} from 'vue'
import {useRouter} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import {get,post,put,del} from '@/utils/request'
import OrgTreeNode from '@/components/OrgTreeNode.vue'
const router=useRouter(),tab=ref('chart'),loading=ref(false),saving=ref(false)
const departments=ref<any[]>([]),people=ref<any[]>([]),positions=ref<any[]>([]),selectedDepartment=ref<number>(),collapsed=ref(new Set<string>())
const departmentVisible=ref(false),positionVisible=ref(false),membersVisible=ref(false),positionsVisible=ref(false),members=ref<any[]>([])
const departmentForm=ref<any>({}),positionForm=ref<any>({})
function departmentPath(id:number){const parts:string[]=[],seen=new Set<number>();let d=departments.value.find(d=>d.id===id);while(d&&!seen.has(d.id)){seen.add(d.id);parts.unshift(d.name);d=departments.value.find(p=>p.id===d.parent_id)}return parts.join(' / ')}
function personDepartment(p:any){return p.department_id || (departments.value.filter(d=>d.name===p.department_name).length===1?departments.value.find(d=>d.name===p.department_name)?.id:null)}
const tree=computed(()=>{const nodes=new Map<number,any>(departments.value.map(d=>[d.id,{...d,node_type:'department',children:[],manager_name:people.value.find(p=>p.id===d.manager_id)?.name,employee_count:people.value.filter(p=>personDepartment(p)===d.id&&p.is_active&&['在职','试用'].includes(p.status)).length}]));const roots:any[]=[];for(const node of nodes.values()){const parent=nodes.get(node.parent_id);if(parent)parent.children.push(node);else roots.push(node)}return roots})
const filteredPositions=computed(()=>positions.value.filter(p=>!selectedDepartment.value||p.department_id===selectedDepartment.value))
const onboard=computed(()=>filteredPositions.value.reduce((n,p)=>n+p.member_count,0))
const parentOptions=computed(()=>departments.value.filter(d=>{if(d.level>=4)return false;let current:any=d;const seen=new Set<number>();while(current&&!seen.has(current.id)){if(current.id===departmentForm.value.id)return false;seen.add(current.id);current=departments.value.find(p=>p.id===current.parent_id)}return true}))
function error(e:any){ElMessage.error(typeof e.response?.data?.detail==='string'?e.response.data.detail:'操作失败，请稍后重试')}
async function load(){loading.value=true;try{const [d,p,j]=await Promise.all([get<any[]>('/departments'),get<any[]>('/field/people'),get<any[]>('/field/positions')]);departments.value=d;people.value=p;positions.value=j}catch(e){error(e)}finally{loading.value=false}}
function toggle(key:string){const next=new Set(collapsed.value);next.has(key)?next.delete(key):next.add(key);collapsed.value=next}
function editDepartment(d?:any){departmentForm.value=d?{id:d.id,name:d.name,parent_id:d.parent_id,manager_id:d.manager_id}:{name:'',parent_id:null,manager_id:null};departmentVisible.value=true}
function addChild(d:any){if(d.level>=4){ElMessage.warning('组织架构最多支持四级');return}editDepartment();departmentForm.value.parent_id=d.id}
async function saveDepartment(){if(saving.value)return;if(!departmentForm.value.name?.trim()){ElMessage.warning('请填写部门名称');return}saving.value=true;try{const d=departmentForm.value;const payload={name:d.name.trim(),parent_id:d.parent_id||null,manager_id:d.manager_id||null};d.id?await put(`/departments/${d.id}`,payload):await post('/departments',payload);departmentVisible.value=false;await load();ElMessage.success('部门已保存')}catch(e){error(e)}finally{saving.value=false}}
async function removeDepartment(d:any){try{await ElMessageBox.confirm(`停用“${d.name}”？已有人员资料将保留。`,'停用部门',{type:'warning'});await del(`/departments/${d.id}`);await load()}catch(e){if(e!=='cancel'&&e!=='close')error(e)}}
function viewDepartment(id:number){members.value=people.value.filter(p=>personDepartment(p)===id);membersVisible.value=true}
async function savePosition(){if(saving.value)return;const p=positionForm.value;if(!p.department_id||!p.name?.trim()){ElMessage.warning('请选择部门并填写岗位名称');return}saving.value=true;try{await post('/field/positions',{department_id:p.department_id,name:p.name.trim()});positionVisible.value=false;selectedDepartment.value=p.department_id;await load();ElMessage.success('岗位已新增，可在人员花名册中选择')}catch(e){error(e)}finally{saving.value=false}}
onMounted(load)
</script>
<style scoped>
.org-page{max-width:1600px;margin:auto}.org-chart{display:flex;gap:36px;overflow:auto;padding:36px 20px;min-height:400px}.position-toolbar{display:flex;justify-content:space-between;gap:16px;margin:8px 0 20px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}.stats>div{border:1px solid #e6eaf2;border-top:3px solid #6554ef;border-radius:12px;padding:20px;display:flex;flex-direction:column;gap:10px}.stats>div:nth-child(2){border-top-color:#14a5e8}.stats>div:nth-child(3){border-top-color:#f27670}.stats>div:nth-child(4){border-top-color:#13b991}.stats span,.stats small,.department-label{color:#7a8498}.stats strong{font-size:30px;color:#1f2937}.position-list{display:flex;gap:16px;flex-wrap:wrap}.position-card{border:1px solid #e6eaf2;border-radius:12px;padding:20px;flex:0 1 310px}.position-card h3{display:flex;align-items:center;gap:12px;margin:12px 0}.position-card p{line-height:1.8;color:#64748b}.department-label{font-size:12px}@media(max-width:800px){.stats{grid-template-columns:repeat(2,1fr)}}
</style>
