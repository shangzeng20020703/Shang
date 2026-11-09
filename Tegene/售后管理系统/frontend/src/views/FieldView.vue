<template>
  <div v-loading="loading">
    <div class="page-intro">
      <div><h1>项目库</h1><p>分配项目负责范围、确认人员和个人排班后，再配置现场轮休规则。</p></div>
      <div><el-button v-if="canImportProjects" type="primary" plain @click="openProjectImport">导入项目与打卡位置</el-button><el-button v-if="canManageGlobalRules || canManageProjectRules" @click="router.push('/attendance?tab=rules')">配置考勤规则</el-button><el-button :loading="sourceLoading" @click="refresh">刷新项目</el-button></div>
    </div>
    <div class="workflow"><span><b>1</b>分配区域组长</span><span><b>2</b>确认项目人员</span><span><b>3</b>逐人确认排班</span><span><b>4</b>配置现场轮休规则</span><span><b>5</b>规则生效后打卡</span></div>
    <el-alert v-if="error || sourceError || catalogError" :title="error || sourceError || catalogError" type="error" :closable="false" show-icon class="notice"/>
    <el-alert v-if="canManageGlobalRules && sourceConfigured === false" title="Tegene 项目来源尚未连接；已导入的项目表仍可查看和配置。" type="info" :closable="false" class="notice"/>
    <section class="surface">
      <div class="field-toolbar">
        <el-input v-model="query" placeholder="搜索项目名称、编号、项目组长" clearable style="max-width:340px"/>
        <el-select v-model="stage" aria-label="配置状态" style="width:155px"><el-option label="全部项目" value="all"/><el-option label="已配置规则" value="ready"/><el-option label="待配置规则" value="pending"/></el-select>
        <span class="list-count">共 {{rows.length}} 个项目</span>
      </div>
      <el-table :data="pagedRows" class="field-table" empty-text="暂无匹配项目" @row-click="openProject">
        <el-table-column prop="code" label="项目编号" width="140"/>
        <el-table-column prop="name" label="项目名称" min-width="260"/>
        <el-table-column label="项目地点" min-width="180" show-overflow-tooltip><template #default="{row}"><span v-if="row.catalog_entry?.address">{{row.catalog_entry.address}}</span><span v-else-if="hasConfiguredProjectLocation(row)">表格未提供；规则已配置</span><span v-else-if="row.catalog_entry?.require_gps" class="location-missing">需在规则中设置</span><span v-else class="muted">未录入</span></template></el-table-column>
        <el-table-column label="定位" width="110"><template #default="{row}"><el-tag v-if="row.catalog_entry" :type="row.catalog_entry.require_gps ? (row.catalog_entry.has_location || hasConfiguredProjectLocation(row) ? 'success' : 'warning') : 'info'">{{row.catalog_entry.require_gps ? (hasConfiguredProjectLocation(row) && !row.catalog_entry.has_location ? '规则已配置' : '需要定位') : '无需定位'}}</el-tag><span v-else class="muted">按规则</span></template></el-table-column>
        <el-table-column label="项目组长" width="120"><template #default="{row}">{{row.manager_name || '待指定'}}</template></el-table-column>
        <el-table-column label="考勤规则" min-width="180"><template #default="{row}"><span v-if="row.rule_name">{{row.rule_name}}</span><el-tag v-else type="info">{{projectStageLabel(row)}}</el-tag></template></el-table-column>
        <el-table-column label="参与人员" min-width="205"><template #default="{row}"><template v-if="savedMemberCount(row)"><strong>已保存 {{savedMemberCount(row)}} 人</strong><div class="member-count-detail">{{row.member_count || 0}} 人已生效<span v-if="pendingCount(row)"> · {{pendingCount(row)}} 人{{row.rule_id ? '待启用' : '待排班及规则生效'}}</span><span v-if="!row.rule_id && row.pre_rule_schedule_ready"> · 个人排班已全部确认</span></div></template><span v-else class="muted">人员待确认</span></template></el-table-column>
        <el-table-column label="操作" width="115" fixed="right"><template #default="{row}"><el-button link type="primary" @click.stop="openProject(row)">{{canManage(row) ? '配置项目' : '查看项目'}}</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="PAGE_SIZE" :total="rows.length" layout="prev, pager, next" class="pagination"/>
      <p class="muted">每页 50 行。项目资料来自 Tegene 或已导入的项目表；人员与本项目专用规则保存在售后系统。</p>
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
        <el-alert v-if="selected.catalog_entry?.require_gps && !selected.catalog_entry?.has_location && !hasConfiguredProjectLocation(selected)" title="本项目需要定位，但表格没有有效位置。创建打卡规则时请手动设置地点和范围。" type="warning" :closable="false" show-icon class="notice"/>
        <section v-if="selected.catalog_entry" class="config-section"><h3>项目表中的地点</h3><p>{{selected.catalog_entry.address || (hasConfiguredProjectLocation(selected) ? '表格未提供；本项目规则已配置打卡位置' : '尚未录入地理位置') }} · {{selected.catalog_entry.require_gps ? '需要定位' : '无需定位'}}</p><p v-if="selected.catalog_entry.has_location" class="muted">坐标 {{selected.catalog_entry.latitude}}, {{selected.catalog_entry.longitude}}；新建本项目规则时会预填 300 米打卡范围。</p><p v-else-if="hasConfiguredProjectLocation(selected)" class="muted">实际打卡范围以本项目已保存的规则为准。</p><p v-if="selected.catalog_entry.rule_updates_pending" class="location-missing">已导入的新地点尚未应用到现有规则；请由有权限的人员核对后确认更新。</p></section>
        <el-alert v-if="!selected.id" title="项目资料尚未同步到本地项目库，请刷新后重试。" type="warning" :closable="false" show-icon class="notice"/>
        <div v-else>
        <section class="config-section">
          <h3>1. 区域组长负责范围</h3>
          <p class="muted">售后经理可先分配主责或协管区域组长，再由获分配的区域组长确认项目人员。区域组长能查看所有项目，但只能修改自己负责的项目。</p>
          <el-form label-position="top">
            <div class="config-grid">
              <el-form-item label="主责区域组长"><el-select v-model="regionalForm.primary_lead_id" clearable filterable placeholder="选择主责" :disabled="!data.can_assign_regionals || saving"><el-option v-for="p in regionalLeads" :key="p.id" :label="p.name" :value="p.id" /></el-select></el-form-item>
              <el-form-item label="协管区域组长（可选）"><el-select v-model="regionalForm.assistant_lead_ids" multiple filterable placeholder="可留空" :disabled="!data.can_assign_regionals || saving"><el-option v-for="p in regionalLeads" :key="p.id" :label="p.name" :value="p.id" :disabled="p.id===regionalForm.primary_lead_id" /></el-select></el-form-item>
            </div>
            <el-button v-if="data.can_assign_regionals" type="primary" :loading="saving" @click="saveRegionalLeads">保存区域分配</el-button>
          </el-form>
        </section>
        <div v-if="pendingMembers.length && !localProject?.rule_id" class="saved-roster notice">
          <el-alert :title="pendingRosterConfirmed ? `已确认人员配置：组长及${savedPendingGroupIds.length}名组员，共${savedMemberCount(localProject)}人；${localProject?.pre_rule_schedule_ready ? '每人排班均已确认，可建立规则' : '下一步逐人确认排班'}` : '已保存的人员名单有未确认修改，请再次确认人员配置'" :type="pendingRosterConfirmed ? 'success' : 'warning'" :closable="false" show-icon/>
          <el-button v-if="pendingRosterConfirmed" type="primary" @click="openRotation">{{localProject?.pre_rule_schedule_ready ? '查看已确认个人排班' : '下一步：逐人确认排班'}}</el-button>
        </div>
        <section class="config-section">
          <h3>{{localProject?.rule_id ? '2. 指定项目组长' : '2. 确认项目人员'}}</h3>
          <el-form label-position="top">
            <el-form-item label="项目组长（每项目一位）">
              <el-select v-model="config.manager_id" filterable placeholder="从项目组长候选人中选择" :disabled="!canManage(selected) || saving" style="max-width:420px">
                <el-option v-for="p in managers" :key="p.id" :label="`${p.name} · ${p.employee_no}`" :value="p.id" :disabled="!p.is_active"/>
              </el-select>
            </el-form-item>
            <template v-if="!localProject?.rule_id">
              <el-form-item label="项目组员（可多选）"><el-select v-model="memberForm.employee_ids" multiple filterable placeholder="仅显示组员候选人" :disabled="!canManage(selected) || saving"><el-option v-for="p in memberCandidates" :key="p.id" :value="p.id" :label="`${p.name} · ${p.phone || p.employee_no}`"/></el-select></el-form-item>
              <p class="muted">一次确认组长及完整组员名单；项目组长会自动加入。再次确认会以当前选择替换原待配置组员。已确认排班的人员不能直接移出，请先处理其排班；规则生效前不能按本项目打卡。</p>
              <el-button v-if="canManage(selected)" type="primary" :loading="saving" :disabled="!config.manager_id || !memberForm.employee_ids.length" @click="confirmPendingRoster">确认人员配置</el-button>
              <span v-if="pendingRosterConfirmed" class="muted roster-confirmed">人员配置已确认，刷新后仍会保留。</span>
            </template>
            <template v-else>
              <p class="muted">保存后，项目组长自动加入本项目。已有项目的人员请使用下方的人员维护和调动。</p>
              <el-button v-if="canManage(selected)" type="primary" :loading="saving" :disabled="!config.manager_id || !configDirty" @click="saveConfig">保存项目组长</el-button>
            </template>
          </el-form>
        </section>
        <section class="config-section">
          <div class="section-title"><h3>人员名单 <small>已保存 {{savedMemberCount(localProject)}} 人（{{activeMemberCount}} 人已生效，{{pendingMembers.length}} 人待启用）</small></h3><el-checkbox v-model="showHistory">显示历史</el-checkbox></div>
          <template v-if="canManage(selected) && localProject?.rule_id">
            <p v-if="!selected.id || configDirty" class="muted">请先保存上方的项目组长，再分配组员。</p>
            <el-form label-position="top" class="members-form">
              <el-form-item label="选择组员（可多选）"><el-select v-model="memberForm.employee_ids" multiple filterable placeholder="仅显示组员" :disabled="saving"><el-option v-for="p in memberCandidates" :key="p.id" :value="p.id" :label="`${p.name} · ${p.phone || p.employee_no}`" :disabled="data.assignments.some((a:any)=>a.employee_id===p.id && a.is_current) || pendingMembers.some((a:any)=>a.employee_id===p.id)"/></el-select></el-form-item>
              <div class="member-actions"><span class="muted">已有项目的人员请使用调动。</span><el-button type="primary" plain :loading="saving" :disabled="!memberAssignmentReady || !memberForm.employee_ids.length" @click="addMembers">分配组员到项目</el-button><el-button :disabled="saving || !memberAssignmentReady" @click="openMemberImport">批量导入组员</el-button></div>
            </el-form>
          </template>
          <el-table v-if="pendingMembers.length" :data="pendingMembers" class="field-table" empty-text="暂无待配置人员">
            <el-table-column prop="employee_name" label="待配置人员" width="150"/>
            <el-table-column label="状态"><template #default>{{localProject?.rule_id ? '名单已保存，待统一启用；尚不能打卡' : '名单已保存，待确认排班并建立规则；尚不能打卡'}}</template></el-table-column>
            <el-table-column v-if="canManage(selected)" label="操作" width="190"><template #default="{row}"><el-button v-if="!localProject?.rule_id && draftEmployeeIds.has(Number(row.employee_id))" link type="warning" :disabled="saving" @click="revokePreRuleSchedule(row)">撤销待生效排班</el-button><span v-else-if="row.is_leader_auto" class="muted">更换组长后自动更新</span><span v-else-if="!localProject?.rule_id" class="muted">在上方取消选择后确认</span><el-button v-else link type="danger" :disabled="saving" @click="removePendingMember(row)">移出</el-button></template></el-table-column>
          </el-table>
          <el-table :data="members" empty-text="暂无参与人员" class="field-table">
            <el-table-column prop="employee_name" label="人员" width="120"/>
            <el-table-column label="参与时间" min-width="210"><template #default="{row}">{{formatTime(row.started_at)}} — {{row.ended_at?formatTime(row.ended_at):'至今'}}</template></el-table-column>
            <el-table-column label="状态" width="110"><template #default="{row}"><el-tag :type="row.is_current && row.can_punch ? 'success' : 'info'">{{memberState(row)}}</el-tag></template></el-table-column>
            <el-table-column v-if="canManage(selected)" label="操作" width="150"><template #default="{row}"><template v-if="row.is_current || (row.status==='active' && row.start_date>today)"><el-button v-if="row.is_current" link type="primary" :disabled="saving" @click="openTransfer(row)">调动</el-button><el-button link type="danger" :disabled="saving" @click="cancelMember(row)">移出</el-button></template></template></el-table-column>
          </el-table>
        </section>
        <section class="config-section">
          <h3>3. 确认个人排班</h3>
          <template v-if="!localProject?.rule_id">
            <p class="muted">组长和组员分别选择起始日期及上六休一、上十二休二、上十八休三或自定义安排，并各自确认至少一个完整周期。可把未来项目未知的日期标为“待分配项目”；规则保存前只是待生效计划，不能打卡或攒休。</p>
            <el-alert v-if="localProject?.pre_rule_schedule_ready" :title="`个人排班已全部确认：${pendingMembers.length} 人；可建立现场轮休规则`" type="success" :closable="false" show-icon class="notice"/>
            <el-alert v-else-if="pendingRosterConfirmed" :title="`已确认 ${localProject?.pre_rule_schedule_member_count || 0} / ${pendingMembers.length} 人的完整个人周期；仍需确认：${pendingScheduleMissingNames}`" type="warning" :closable="false" show-icon class="notice"/>
          </template>
          <p v-else class="muted">规则已配置，可查看个人排班和申请后续调整。</p>
          <el-button type="primary" :disabled="!canOpenRotation" @click="openRotation">{{localProject?.rule_id ? '打开本项目个人排班' : '打开个人排班并逐人确认'}}</el-button>
        </section>
        <section class="config-section">
          <h3>4. 建立本项目考勤规则</h3>
          <p v-if="chosenRule" class="rule-summary">{{chosenRule.name}} · {{ruleSummary(chosenRule)}}</p>
          <p v-if="!localProject?.rule_id" class="muted">组长和全部组员各自确认一个完整排班周期后，才能新建“现场轮休”规则并设置每日工时、打卡地点等要求。保存规则时统一校验并启用人员和属于本项目的待生效日期；有冲突会列出具体原因，整笔保持待配置。</p>
          <p v-else class="muted">本项目已有考勤规则。修改规则仅作用于本项目，原有打卡记录保留。</p>
          <div class="member-actions">
            <el-button v-if="canManage(selected)" type="primary" :disabled="!canOpenProjectRule" @click="openProjectRule">{{localProject?.rule_id ? '编辑本项目规则' : '新建现场轮休规则'}}</el-button>
            <el-button v-if="canManage(selected) && localProject?.rule_id && pendingMembers.length" :loading="saving" @click="activatePendingMembers">重试启用待配置人员</el-button>
          </div>
          <p v-if="canManage(selected) && !localProject?.rule_id && !pendingRosterConfirmed" class="muted">请先在上方选择项目组长和至少一名组员，点击“确认人员配置”。</p>
          <p v-else-if="canManage(selected) && !localProject?.rule_id && !localProject?.pre_rule_schedule_ready" class="muted">请先在本项目的个人排班中逐人确认完整周期。</p>
          <el-alert v-if="activationError" :title="activationError" type="error" :closable="false" show-icon class="notice"/>
        </section>
        <section v-if="selected.catalog_entry && !selected.external_id" class="config-section">
          <h3>客户资料（可稍后补录）</h3>
          <p class="muted">客户资料不影响人员确认、个人排班或打卡规则。当前客户：{{localProject?.customer_name || '尚未填写'}}</p>
          <el-form v-if="canImportProjects" label-position="top">
            <el-form-item label="客户"><el-select v-model="customerForm" filterable placeholder="可稍后选择" :disabled="saving"><el-option v-for="customer in data.customers || []" :key="customer.id" :label="customer.name" :value="customer.id" :disabled="customer.is_active===false"/></el-select><div class="customer-create"><el-input v-model="newCustomerName" maxlength="120" placeholder="没有客户时，在此输入新客户名称" :disabled="saving"/><el-button :loading="saving" :disabled="!newCustomerName.trim()" @click="createCatalogCustomer">新建客户</el-button></div></el-form-item>
            <el-button type="primary" plain :loading="saving" :disabled="!customerForm || customerForm===localProject?.customer_id" @click="saveCatalogCustomer">保存客户资料</el-button>
          </el-form>
        </section>
        </div>
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
    <el-dialog v-model="projectImportVisible" title="导入项目与打卡位置" width="min(1080px,96vw)" destroy-on-close :close-on-click-modal="!projectImportSaving">
      <p>选择按模板填写的 .xlsx 文件。只读取“项目编号、项目名称、地理位置、是否需要定位”；导入前先预览新增、变更和缺少位置的项目。</p>
      <div class="field-toolbar"><input type="file" accept=".xlsx" aria-label="项目位置表格" :disabled="projectImportSaving" @change="selectProjectFile"/><el-button :disabled="!projectFile" :loading="projectImportSaving" @click="previewProjectImport">预览差异</el-button></div>
      <el-alert v-if="projectImportError" :title="projectImportError" type="error" :closable="false" show-icon class="notice"/>
      <template v-if="projectImportReport">
        <p class="import-summary">共 {{projectImportReport.summary?.total || 0}} 个项目 · 新增 {{projectImportReport.summary?.create || 0}} · 变更 {{projectImportReport.summary?.update || 0}} · 不变 {{projectImportReport.summary?.unchanged || 0}} · 需要定位但缺少位置 {{projectImportReport.summary?.missing_location || 0}}</p>
        <el-alert v-if="projectImportReport.rows?.some((row:any)=>row.active_rule_change)" title="已有生效规则的地点或定位要求发生变化时，逐项勾选才会更新规则；未勾选的规则保持原样。" type="warning" :closable="false" class="notice"/>
        <el-table :data="projectImportReport.rows || []" max-height="450" class="field-table" empty-text="无可导入项目">
          <el-table-column prop="row" label="行" width="58"/>
          <el-table-column prop="code" label="项目编号" width="110"/>
          <el-table-column prop="name" label="项目名称" min-width="220" show-overflow-tooltip/>
          <el-table-column label="地理位置" min-width="180" show-overflow-tooltip><template #default="{row}">{{row.address || '未填写'}}<span v-if="row.require_gps && !row.has_location" class="location-missing"> · 规则中需补充</span></template></el-table-column>
          <el-table-column label="定位" width="85"><template #default="{row}">{{row.require_gps ? '需要' : '不需要'}}</template></el-table-column>
          <el-table-column label="结果" min-width="155"><template #default="{row}"><span v-if="row.error" class="location-missing">{{row.error}}</span><template v-else><el-tag :type="importStatusType(row.status)">{{importStatusLabel(row.status)}}</el-tag><span v-if="row.changes?.length" class="import-diff">{{importDiffText(row.changes)}}</span></template></template></el-table-column>
          <el-table-column label="更新现有规则" width="125"><template #default="{row}"><span v-if="row.active_rule_change && row.require_gps && !row.has_location" class="location-missing">先补位置</span><el-checkbox v-else-if="row.active_rule_change && row.local_project_id" :model-value="confirmedRuleProjectIds.includes(row.local_project_id)" @change="(checked:boolean)=>toggleRuleProject(row.local_project_id,checked)">确认</el-checkbox><span v-else class="muted">—</span></template></el-table-column>
        </el-table>
      </template>
      <template #footer><el-button @click="projectImportVisible=false">取消</el-button><el-button type="primary" :loading="projectImportSaving" :disabled="!projectImportReport?.preview_token || projectImportReport.rows?.some((row:any)=>!!row.error)" @click="commitProjectImport">确认导入项目</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import {ref, computed, onMounted, watch} from 'vue'
import {useRouter} from 'vue-router'
import request, {get, post, put, del} from '@/utils/request'
import {ElMessage, ElMessageBox} from 'element-plus'
import {getWebAuthRoles,isWebAuthSuperuser} from '@/utils/authSession'
const router = useRouter()
const PAGE_SIZE = 50
const data = ref<any>({projects:[], assignments:[], pending_members:[], people:[], rules:[], managed_project_ids:[],regional_assignments:[],can_assign_regionals:false})
const loading=ref(false), saving=ref(false), error=ref(''), sourceError=ref(''), sourceLoading=ref(false), sourceConfigured=ref<boolean|null>(null)
const catalogError=ref(''), importedCatalog=ref<any[]>([])
const sourceProjects=ref<any[]>([]), query=ref(''), stage=ref('all'), page=ref(1), visible=ref(false), selected=ref<any>(null), formError=ref(''), activationError=ref(''), showHistory=ref(false)
const config=ref<any>({manager_id:undefined, rule_id:undefined})
const customerForm=ref<number|undefined>()
const newCustomerName=ref('')
const regionalForm=ref<{primary_lead_id:number|undefined,assistant_lead_ids:number[]}>({primary_lead_id:undefined,assistant_lead_ids:[]})
const regionalPeople=ref<any[]>([])
const transferRequests=ref<any[]>([])
const memberForm=ref<{employee_ids:number[]}>({employee_ids:[]})
const transferVisible=ref(false), transferPerson=ref<any>(null), transferProject=ref<number>(), transferDate=ref(''), transferReason=ref(''), importVisible=ref(false), memberFile=ref<File|null>(null), importReport=ref<any>(null)
const projectImportVisible=ref(false), projectImportSaving=ref(false), projectFile=ref<File|null>(null), projectImportReport=ref<any>(null), projectImportError=ref(''), confirmedRuleProjectIds=ref<number[]>([])
const transferTargets=computed(()=>data.value.projects.filter((p:any)=>p.id!==selected.value?.id && p.rule_id && p.is_active!==false))
const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
const upstreamConfig={timeout:30000,skipErrorReport:true,skipDefaultErrorHandler:true}
const catalog=computed(()=>{
  const importedByCode=new Map(importedCatalog.value.map((entry:any)=>[String(entry.code || '').toUpperCase(),entry]))
  const usedLocalIds=new Set<number>()
  const result:any[]=[]
  const append=(row:any)=>{
    const code=String(row.code || '').toUpperCase()
    const entry=importedByCode.get(code)
    result.push({...row,name:entry?.name || row.name,catalog_entry:entry || null,catalog_only:false})
    if(row.id)usedLocalIds.add(Number(row.id))
  }
  for(const source of sourceProjects.value){
    const local=data.value.projects.find((project:any)=>project.id===source.local_project_id || String(project.code || '').toUpperCase()===String(source.projectNo || '').toUpperCase())
    append({...local,external_id:local && !source.local_project_id ? null : source.id,code:source.projectNo,name:source.name})
  }
  for(const local of data.value.projects){if(!usedLocalIds.has(Number(local.id)))append({...local})}
  const usedCodes=new Set(result.map((row:any)=>String(row.code || '').toUpperCase()))
  for(const entry of importedCatalog.value){
    if(usedCodes.has(String(entry.code || '').toUpperCase()))continue
    result.push({id:null,external_id:null,code:entry.code,name:entry.name,manager_name:'',member_count:0,rule_id:null,is_active:true,catalog_only:true,catalog_entry:entry})
  }
  return result
})
const rows=computed(()=>catalog.value.filter((p:any)=>(stage.value==='all' || (stage.value==='ready' ? !!p.rule_id : !p.rule_id)) && [p.name,p.code,p.manager_name].join(' ').toLowerCase().includes(query.value.trim().toLowerCase())))
const pagedRows=computed(()=>rows.value.slice((page.value-1)*PAGE_SIZE,page.value*PAGE_SIZE))
const localProject=computed(()=>data.value.projects.find((p:any)=>p.id===selected.value?.id))
const canManageGlobalRules=computed(()=>isWebAuthSuperuser()||getWebAuthRoles().some(r=>['admin','hr'].includes(r)))
const canManageProjectRules=computed(()=>getWebAuthRoles().some(r=>['aftersales_manager','regional_lead'].includes(r)))
const canImportProjects=computed(()=>isWebAuthSuperuser()||getWebAuthRoles().some(r=>['admin','aftersales_manager'].includes(r)))
function hasConfiguredProjectLocation(project:any){return !!(project?.catalog_entry?.active_rule_has_valid_geofence && project?.catalog_entry?.active_rule_requires_gps)}
const regionalLeads=computed(()=>{
  const assigned=new Set([regionalForm.value.primary_lead_id,...regionalForm.value.assistant_lead_ids])
  return regionalPeople.value.filter((p:any)=>p.is_active!==false && (p.role==='regional_lead' || assigned.has(p.id)))
})
const members=computed(()=>data.value.assignments.filter((a:any)=>a.project_id===selected.value?.id && (showHistory.value || a.is_current)))
const pendingMembers=computed<any[]>(()=>Array.isArray(data.value.pending_members)?data.value.pending_members.filter((a:any)=>a.project_id===selected.value?.id):[])
function pendingCount(project:any){return Array.isArray(data.value.pending_members)?data.value.pending_members.filter((a:any)=>a.project_id===project.id).length:0}
function savedMemberCount(project:any){return project ? (project.member_count || 0)+pendingCount(project) : 0}
function projectStageLabel(project:any){
  if(!project.id)return '资料同步中'
  if(!savedMemberCount(project))return '人员待确认'
  if(!project.pre_rule_schedule_ready)return '排班待确认'
  return '规则待建立'
}
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
const configDirty=computed(()=>config.value.manager_id!==localProject.value?.manager_id || config.value.rule_id!==localProject.value?.rule_id)
const savedPendingGroupIds=computed<number[]>(()=>pendingMembers.value.filter((a:any)=>!a.is_leader_auto).map((a:any)=>Number(a.employee_id)).sort((a,b)=>a-b))
const pendingRosterDirty=computed(()=>{
  if(!localProject.value)return true
  const selectedIds=[...new Set(memberForm.value.employee_ids)].sort((a,b)=>a-b)
  return config.value.manager_id!==localProject.value.manager_id || selectedIds.length!==savedPendingGroupIds.value.length || selectedIds.some((id,index)=>id!==savedPendingGroupIds.value[index])
})
const pendingRosterConfirmed=computed(()=>Boolean(localProject.value?.manager_id && savedPendingGroupIds.value.length && !pendingRosterDirty.value))
const pendingScheduleMissingNames=computed(()=>{
  const ids=Array.isArray(localProject.value?.pre_rule_schedule_missing_member_ids) ? localProject.value.pre_rule_schedule_missing_member_ids : []
  if(!ids.length)return pendingMembers.value.map((row:any)=>row.employee_name || `人员${row.employee_id}`).join('、') || '全部人员'
  return ids.map((id:number)=>pendingMembers.value.find((row:any)=>Number(row.employee_id)===Number(id))?.employee_name || `人员${id}`).join('、')
})
const draftEmployeeIds=computed(()=>new Set<number>(Array.isArray(localProject.value?.pre_rule_schedule_employee_ids) ? localProject.value.pre_rule_schedule_employee_ids.map(Number) : []))
const canOpenRotation=computed(()=>Boolean(selected.value?.id && (selected.value?.can_schedule ?? canManage(selected.value)) && (localProject.value?.rule_id ? rotationRuleReady.value && activeMemberCount.value > 0 && !pendingMembers.value.length : pendingRosterConfirmed.value)))
const canOpenProjectRule=computed(()=>Boolean(selected.value?.id && (localProject.value?.rule_id ? !configDirty.value : pendingRosterConfirmed.value && localProject.value?.pre_rule_schedule_ready)))
const memberAssignmentReady=computed(()=>Boolean(selected.value?.id && config.value.manager_id && !configDirty.value))
function canManage(p:any){
  if(!p || p.is_active===false || !p.id)return false
  return p.can_manage ?? data.value.managed_project_ids.includes(p.id)
}
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
async function loadCatalog(){
  catalogError.value=''
  try{const result=await get('/field/projects/catalog');importedCatalog.value=Array.isArray(result?.items)?result.items:[]}
  catch(e){catalogError.value=message(e);importedCatalog.value=[]}
}
async function refresh(){
  loading.value=true;error.value='';sourceError.value=''
  try{await loadLocal();await loadCatalog();if(canManageGlobalRules.value || canManageProjectRules.value){sourceLoading.value=true;try{const result=await get('/field/tegene/projects',undefined,upstreamConfig);sourceProjects.value=result.projects;sourceConfigured.value=result.configured}catch(e){sourceProjects.value=[];sourceError.value=message(e)+'；已配置的项目仍可使用。'}finally{sourceLoading.value=false}}}catch(e){error.value=message(e)}finally{loading.value=false}
}
function openProject(row:any){selected.value=row;config.value={manager_id:row.manager_id,rule_id:row.rule_id};customerForm.value=row.customer_id || undefined;newCustomerName.value='';regionalForm.value={primary_lead_id:row.primary_regional_lead_id||undefined,assistant_lead_ids:[...(row.assistant_regional_lead_ids||[])]};memberForm.value={employee_ids:row.rule_id?[]:data.value.pending_members.filter((a:any)=>a.project_id===row.id && !a.is_leader_auto).map((a:any)=>a.employee_id)};showHistory.value=false;formError.value='';activationError.value='';visible.value=true}
function openProjectRule(){if(!canOpenProjectRule.value)return;router.push({path:'/attendance',query:{tab:'rules',project_id:selected.value.id}})}
function openRotation(){if(!canOpenRotation.value)return;router.push({path:'/rotation',query:{project_id:selected.value.id}})}
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
async function confirmPendingRoster(){
  if(!canManage(selected.value) || localProject.value?.rule_id || !config.value.manager_id || !memberForm.value.employee_ids.length)return
  saving.value=true;formError.value=''
  const payload={manager_id:config.value.manager_id,member_ids:[...new Set(memberForm.value.employee_ids)]}
  let result:any
  try{
    result=selected.value.id
      ? await put(`/field/projects/${selected.value.id}/pending-roster`,payload)
      : await post(`/field/tegene/projects/${selected.value.external_id}/pending-roster`,payload,upstreamConfig)
  }catch(e){formError.value=activationConflictMessage(e);saving.value=false;return}
  selected.value.id=result.id
  const source=sourceProjects.value.find(p=>p.id===selected.value.external_id)
  if(source)source.local_project_id=result.id
  try{
    await loadLocal()
    const saved=localProject.value
    if(!saved){ElMessage.warning('人员配置已确认，但本地列表尚未更新；请点击“刷新项目”核对');return}
    config.value={manager_id:saved.manager_id,rule_id:saved.rule_id}
    memberForm.value={employee_ids:[...savedPendingGroupIds.value]}
    selected.value={...selected.value,...saved}
    ElMessage.success('项目人员配置已确认；下一步请确认个人排班')
  }catch{ElMessage.warning('人员配置已确认，但列表刷新失败；请点击“刷新项目”核对')}
  finally{saving.value=false}
}
async function addMembers(){
  saving.value=true;formError.value=''
  try{await post(`/field/projects/${selected.value.id}/members`,{employee_ids:memberForm.value.employee_ids})}
  catch(e){formError.value=message(e);saving.value=false;return}
  memberForm.value={employee_ids:[]}
  try{await loadLocal();ElMessage.success(localProject.value?.rule_id?'人员已加入项目':`人员名单已保存：共${savedMemberCount(localProject.value)}人，下一步确认个人排班`)}
  catch{ElMessage.warning('人员名单已保存，但列表刷新失败；请点击“刷新项目”核对')}
  finally{saving.value=false}
}
async function removePendingMember(row:any){
  saving.value=true;formError.value=''
  try{await del(`/field/projects/${selected.value.id}/pending-members/${row.id}`);await loadLocal();ElMessage.success('已移出待配置人员')}
  catch(e){formError.value=message(e)}finally{saving.value=false}
}
async function revokePreRuleSchedule(row:any){
  if(!selected.value?.id || localProject.value?.rule_id)return
  try{await ElMessageBox.confirm(`撤销 ${row.employee_name} 的全部待生效排班？撤销后可重新确认人员名单。`,'撤销待生效排班',{type:'warning'})}catch{return}
  saving.value=true;formError.value=''
  try{
    const result:any=await del(`/rotation/projects/${selected.value.id}/draft-days`,{params:{employee_id:row.employee_id}})
    await loadLocal()
    ElMessage.success(`已撤销 ${result?.count || 0} 个人日；请重新核对该员工的完整周期`)
  }catch(e){formError.value=activationConflictMessage(e)}finally{saving.value=false}
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
async function saveCatalogCustomer(){
  if(!selected.value?.id || !selected.value.catalog_entry || !canImportProjects.value || !customerForm.value)return
  saving.value=true;formError.value=''
  try{
    await request.patch(`/field/projects/${selected.value.id}/customer`,{customer_id:customerForm.value})
    await loadLocal()
    selected.value={...selected.value,...localProject.value}
    ElMessage.success('客户资料已保存')
  }catch(e){formError.value=message(e)}finally{saving.value=false}
}
async function createCatalogCustomer(){
  if(!selected.value?.id || !selected.value.catalog_entry || !canImportProjects.value || !newCustomerName.value.trim())return
  const name=newCustomerName.value.trim()
  const existing=(data.value.customers || []).find((customer:any)=>customer.is_active!==false && String(customer.name || '').trim()===name)
  if(existing){customerForm.value=existing.id;newCustomerName.value='';ElMessage.info('已选择同名客户');return}
  saving.value=true;formError.value=''
  try{
    const result=await post('/field/customers',{name,contact:'',phone:'',is_active:true})
    await loadLocal()
    customerForm.value=result.id
    newCustomerName.value=''
    ElMessage.success('客户已建立并选中，请点击“保存客户资料”')
  }catch(e){formError.value=message(e)}finally{saving.value=false}
}
function openProjectImport(){projectFile.value=null;projectImportReport.value=null;projectImportError.value='';confirmedRuleProjectIds.value=[];projectImportVisible.value=true}
function selectProjectFile(event:Event){projectFile.value=(event.target as HTMLInputElement).files?.[0] || null;projectImportReport.value=null;projectImportError.value='';confirmedRuleProjectIds.value=[]}
function toggleRuleProject(projectId:number,checked:boolean){
  const ids=new Set(confirmedRuleProjectIds.value)
  if(checked)ids.add(projectId);else ids.delete(projectId)
  confirmedRuleProjectIds.value=[...ids]
}
function importStatusLabel(status:string){return ({create:'新增',update:'变更',unchanged:'不变',error:'错误'} as Record<string,string>)[status] || status}
function importStatusType(status:string){return ({create:'success',update:'warning',unchanged:'info',error:'danger'} as Record<string,string>)[status] || 'info'}
function importDiffText(changes:any[]){
  const names:Record<string,string>={name:'名称',address:'地点',latitude:'纬度',longitude:'经度',require_gps:'定位要求'}
  return changes.map(change=>names[change.field] || change.field).join('、')+'已变更'
}
async function previewProjectImport(){
  if(!projectFile.value)return
  projectImportSaving.value=true;projectImportError.value='';projectImportReport.value=null;confirmedRuleProjectIds.value=[]
  try{
    const form=new FormData();form.append('file',projectFile.value)
    projectImportReport.value=await post('/field/projects/import/preview',form,{timeout:300000})
  }catch(e){projectImportError.value=message(e)}finally{projectImportSaving.value=false}
}
async function commitProjectImport(){
  if(!projectFile.value || !projectImportReport.value?.preview_token)return
  projectImportSaving.value=true;projectImportError.value=''
  try{
    const form=new FormData()
    form.append('file',projectFile.value)
    form.append('preview_token',projectImportReport.value.preview_token)
    form.append('confirm_active_rule_project_ids',JSON.stringify(confirmedRuleProjectIds.value))
    const result=await post('/field/projects/import/commit',form,{timeout:300000})
    await refresh()
    projectImportVisible.value=false
    ElMessage.success(`项目表已导入：新增 ${result.created || 0}、更新 ${result.updated || 0}、不变 ${result.unchanged || 0}`)
  }catch(e){projectImportError.value=message(e)}finally{projectImportSaving.value=false}
}
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
.location-missing{color:#b76013;font-size:12px}.import-summary{color:#365565;font-size:13px;margin:16px 0}.import-diff{display:block;color:#70838f;font-size:12px;line-height:1.5}.field-toolbar input[type=file]{max-width:320px}
.customer-create{display:flex;gap:8px;width:100%;margin-top:8px}.customer-create .el-button{flex:none}@media(max-width:640px){.customer-create{flex-direction:column}}
</style>
