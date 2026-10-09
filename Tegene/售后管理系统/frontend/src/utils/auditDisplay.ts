// Keep stored audit codes intact; translate only the presentation.
export const projectResources = ['save_customer','save_project','save_site','configure_project','create_assignment','cancel_assignment','add_project_members','remove_or_transfer','import_members','field_project']
export const peopleResources = ['save_person','roster','Employee']
const modules: Record<string,string> = {field:'项目库',employee:'人员花名册',attendance:'考勤管理',approval:'审批管理',leave:'考勤管理',organization:'组织架构',company:'公司管理',system:'系统设置'}
const actions: Record<string,string> = {save:'保存',create:'新增',update:'修改',delete:'删除',cancel:'取消安排',configure:'配置项目', 'add-members':'添加项目人员','import-members':'导入项目人员','remove-member':'移出项目','transfer-member':'调动人员',import:'导入花名册',tegene_project_sync:'同步项目资料',balance_adjust:'调整假期余额',approve:'审批通过',reject:'审批驳回',withdraw:'撤回审批',login:'登录',logout:'退出登录',export:'导出'}
const resources: Record<string,string> = {save_customer:'客户资料',save_project:'项目资料',save_site:'项目打卡配置',configure_project:'负责人及打卡规则',create_assignment:'人员安排',cancel_assignment:'人员安排',add_project_members:'项目人员',remove_or_transfer:'项目人员',import_members:'项目人员',field_project:'项目资料',save_person:'人员资料',roster:'人员花名册',Employee:'人员资料',FieldPosition:'部门岗位',LeaveBalance:'假期余额',ApprovalInstance:'审批单',AttendanceRule:'考勤规则',TencentBrowserMapKey:'腾讯地图浏览器 Key'}
const fields: Record<string,string> = {id:'记录编号',name:'名称',code:'编号',phone:'手机号',contact:'联系人',employee_no:'工号',position:'岗位',department_id:'部门编号',direct_manager_id:'直属主管编号',is_active:'是否启用',status:'状态',role:'角色',wecom_userid:'企微账号',password_changed:'密码已重设',customer_id:'客户编号',manager_id:'项目负责人编号',rule_id:'打卡规则编号',project_id:'项目编号',employee_id:'人员编号',employee_ids:'人员编号',site_id:'现场编号',location_id:'地点编号',address:'地址',description:'说明',start_date:'开始日期',end_date:'结束日期',started_at:'生效时间',ended_at:'结束时间',created_at:'创建时间',updated_at:'更新时间',created_by:'创建人编号',source:'来源地址',external_id:'来源项目编号',synced_at:'同步时间',total:'总条数',success:'成功条数',failed:'失败条数',preview:'是否预览',rows:'处理结果',members:'项目人员',row:'行号',error:'失败原因',action:'处理方式',leave_type_id:'假期类型编号',leave_type_name:'假期类型',year:'年度',adjustment:'调整天数',reason:'原因',remaining_days:'剩余天数',total_days:'总天数',used_days:'已用天数',event_id:'关联事件编号',approval_instance_id:'审批单编号',business_id:'业务编号',source_event_id:'来源事件编号',configured:'已配置',configuration_source:'配置来源',has_override:'后台覆盖配置'}
const values: Record<string,string> = {admin:'系统管理员',hr:'人事管理员',manager:'项目负责人',employee:'员工',active:'有效',inactive:'停用',cancelled:'已取消',pending:'待处理',approved:'已通过',rejected:'已驳回',create:'新增',update:'修改',skip:'跳过',import:'导入',failed:'失败',success:'成功',platform:'管理后台',environment:'服务器环境变量',none:'未配置'}
export function moduleLabel(row:any){return peopleResources.includes(row.resource_type)?'人员花名册':modules[row.module] || '其他操作'}
export function actionLabel(row:any){return actions[row.action] || '业务操作'}
export function resourceLabel(row:any){return resources[row.resource_type] || '业务记录'}
function parse(value:any):Record<string,any>{if(!value)return {};try{const result=typeof value==='string'?JSON.parse(value):value;return result && typeof result==='object' && !Array.isArray(result)?result:{}}catch{return {}}}
function label(key:string){return fields[key] || (/^[\u4e00-\u9fff]/.test(key)?key:'其他记录项')}
function display(value:any):string{
 if(value===null || value===undefined || value==='')return '未设置'
 if(typeof value==='boolean')return value?'是':'否'
 if(Array.isArray(value))return value.map(display).join('；') || '无'
 if(typeof value==='object')return Object.entries(value).filter(([k])=>!sensitive(k)).map(([k,v])=>`${label(k)}：${display(v)}`).join('；')
 return values[String(value)] || String(value)
}
function sensitive(key:string){return /password|token|secret|credential/i.test(key) && key!=='password_changed'}
export function changes(row:any){
 const before=parse(row.before_data),after=parse(row.after_data),hasBefore=row.before_data && row.before_data!=='null'
 return [...new Set([...Object.keys(before),...Object.keys(after)])]
  .filter(key=>!sensitive(key) && !['created_at','updated_at'].includes(key) && (!hasBefore || JSON.stringify(before[key])!==JSON.stringify(after[key])))
  .map(key=>({field:label(key),before:hasBefore?display(before[key]):'未记录',after:display(after[key])}))
}
export function summary(row:any){const items=changes(row);return items.length?items.map(i=>i.field).join('、'):(row.before_data?'未变更业务字段':'未记录具体修改项')}
