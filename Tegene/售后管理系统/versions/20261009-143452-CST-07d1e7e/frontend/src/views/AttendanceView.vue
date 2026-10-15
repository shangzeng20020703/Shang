<template>
  <div class="attendance-page" :class="{ 'attendance-page--landscape-compact': isLandscapeCompact }">
    <!-- Page Header -->
    <div class="page-header">
      <div class="page-header__left">
        <h1 class="page-title">考勤管理</h1>
        <p class="page-desc">管理考勤打卡，实时掌握员工出勤状况</p>
      </div>
    </div>

    <!-- Summary Stat Cards -->
    <div class="stat-cards">
      <div class="stat-card">
        <div class="stat-card__icon" style="background: rgba(79,70,229,0.1);">
          <el-icon :size="24" color="#4F46E5"><UserFilled /></el-icon>
        </div>
        <div class="stat-card__info">
          <span class="stat-card__value">
            <span class="stat-card__num">{{ todayStats.present }}</span>
            <span class="stat-card__total">/{{ todayStats.total }}</span>
          </span>
          <span class="stat-card__label">今日出勤</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card__icon" style="background: rgba(245,158,11,0.1);">
          <el-icon :size="24" color="#F59E0B"><Clock /></el-icon>
        </div>
        <div class="stat-card__info">
          <span class="stat-card__num stat-card__num--warning">{{ todayStats.late }}</span>
          <span class="stat-card__label">迟到</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card__icon" style="background: rgba(16,185,129,0.1);">
          <el-icon :size="24" color="#10B981"><Calendar /></el-icon>
        </div>
        <div class="stat-card__info">
          <span class="stat-card__num stat-card__num--success">{{ todayStats.leave }}</span>
          <span class="stat-card__label">请假</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card__icon" style="background: rgba(99,102,241,0.1);">
          <el-icon :size="24" color="#6366F1"><Position /></el-icon>
        </div>
        <div class="stat-card__info">
          <span class="stat-card__num stat-card__num--indigo">{{ todayStats.outside }}</span>
          <span class="stat-card__label">外出/出差</span>
        </div>
      </div>
    </div>

    <div class="trend-cards">
      <el-card class="trend-card" shadow="never">
        <template #header><span>近7日出勤趋势</span></template>
        <VChart :option="presentTrendOption" style="height: 180px" autoresize />
      </el-card>
      <el-card class="trend-card" shadow="never">
        <template #header><span>近7日迟到趋势</span></template>
        <VChart :option="lateTrendOption" style="height: 180px" autoresize />
      </el-card>
      <el-card class="trend-card" shadow="never">
        <template #header><span>近7日请假/出差分布</span></template>
        <VChart :option="leaveDistOption" style="height: 180px" autoresize />
      </el-card>
    </div>

    <!-- Tabs Content -->
    <div class="content-card">
      <el-tabs v-model="activeTab" class="custom-tabs" @tab-change="handleTabChange">
        <!-- Tab: 打卡记录 -->
        <el-tab-pane label="打卡记录" name="records">
          <div class="quick-filter-row">
            <el-tag :effect="quickFilter === 'all' ? 'dark' : 'plain'" round @click="applyQuickFilter('all')">全部</el-tag>
            <el-tag :effect="quickFilter === 'today_anomaly' ? 'dark' : 'plain'" round type="danger" @click="applyQuickFilter('today_anomaly')">今日异常</el-tag>
            <el-tag :effect="quickFilter === 'week_late' ? 'dark' : 'plain'" round type="warning" @click="applyQuickFilter('week_late')">本周迟到</el-tag>
            <el-tag :effect="quickFilter === 'pending_appeal' ? 'dark' : 'plain'" round type="warning" @click="applyQuickFilter('pending_appeal')">待处理申诉</el-tag>
          </div>

          <div class="filter-row">
            <el-tree-select
              v-model="recordDept"
              :data="deptTreeOptions"
              :props="{ label: 'name', value: 'id', children: 'children' }"
              node-key="id"
              :default-expanded-keys="deptDefaultExpandedKeys"
              placeholder="全部部门"
              clearable
              check-strictly
              style="width: 180px"
              @change="handleSearch"
              @visible-change="handleDeptTreeVisibleChange"
            />
            <el-select v-model="recordGroup" placeholder="全部考勤组" clearable style="width: 150px" @change="handleSearch">
              <el-option label="标准工时" value="standard" />
              <el-option label="弹性工时" value="flexible" />
              <el-option label="综合工时" value="comprehensive" />
            </el-select>
            <el-date-picker
              v-model="recordDate"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="YYYY-MM-DD"
              style="width: 300px"
            />
            <el-input v-model="recordKeyword" placeholder="员工姓名/工号" clearable prefix-icon="Search" style="width: 200px" @keyup.enter="fetchRecords" />
            <el-select
              v-model="recordStatuses"
              placeholder="打卡状态"
              multiple
              collapse-tags
              collapse-tags-tooltip
              clearable
              style="width: 180px"
              @change="handleSearch"
            >
              <el-option v-for="status in recordStatusOptions" :key="status" :label="status" :value="status" />
            </el-select>
            <el-button type="primary" @click="fetchRecords">
              <el-icon><Search /></el-icon> 查询
            </el-button>
            <el-button @click="resetRecordFilters">重置</el-button>
            <el-button @click="manualRefresh">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
            <el-dropdown @command="handleExportCommand">
              <el-button>
                <el-icon><Download /></el-icon> 导出
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="current">导出当前列表</el-dropdown-item>
                  <el-dropdown-item command="monthly">导出月报</el-dropdown-item>
                  <el-dropdown-item command="anomaly">导出异常记录</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>

          <div v-if="selectedRecordRows.length" class="batch-toolbar">
            <span>已选 {{ selectedRecordRows.length }} 条</span>
            <el-button size="small" @click="batchMarkAsNormal">批量标记为正常</el-button>
            <el-button size="small" type="warning" @click="batchApproveAppeal">批量通过申诉</el-button>
            <el-button size="small" type="success" @click="batchExportAnomalies">批量导出异常</el-button>
          </div>

          <el-table
            :data="records"
            v-loading="recordLoading"
            class="modern-table"
            :row-class-name="recordRowClass"
            @selection-change="handleRecordSelectionChange"
          >
            <el-table-column type="selection" width="42" />
            <el-table-column label="员工姓名" min-width="170" fixed>
              <template #default="{ row }">
                <el-button link type="primary" class="name-link" @click="openRecordDetail(row)">
                  {{ row.employee_name }}
                </el-button>
                <span class="emp-no">{{ row.employee_no ? `(${row.employee_no})` : '' }}</span>
              </template>
            </el-table-column>
            <el-table-column v-if="!isMobile" prop="department_name" label="部门" min-width="130" />
            <el-table-column prop="date" label="日期" width="120" />
            <el-table-column prop="clock_in" label="上班打卡" width="110" align="right">
              <template #default="{ row }">
                <span :class="['clock-time', clockInClass(row)]">{{ row.clock_in || '--:--' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="clock_out" label="下班打卡" width="110" align="right">
              <template #default="{ row }">
                <span :class="['clock-time', clockOutClass(row)]">{{ row.clock_out || '--:--' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="expected_hours" label="应出勤工时" width="110" align="right" />
            <el-table-column prop="actual_hours" label="实际工时" width="100" align="right">
              <template #default="{ row }">
                <span :class="{ 'text-danger': Number(row.actual_hours) < Number(row.expected_hours) }">{{ row.actual_hours }}h</span>
              </template>
            </el-table-column>
            <el-table-column prop="display_status" label="状态" width="110" align="center">
              <template #default="{ row }">
                <span class="status-pill" :class="`status-pill--${statusClassName(row.display_status)}`">{{ row.display_status }}</span>
              </template>
            </el-table-column>
            <el-table-column v-if="!isMobile" prop="clock_in_location" label="打卡位置" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="location-cell" :class="{ 'location-cell--warn': row.display_location_abnormal, 'location-cell--ok': row.clock_in_location && !row.display_location_abnormal }">
                  <el-icon :size="14"><Location /></el-icon>
                  <span>{{ row.clock_in_location || '-' }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column v-if="!isMobile" prop="device_info" label="打卡设备/IP" min-width="130" show-overflow-tooltip />
            <el-table-column label="操作" width="220">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openRecordDetail(row)">查看详情</el-button>
                <el-button v-if="isExceptionStatus(row.display_status)" link type="warning" size="small" @click="handleSingleAbnormal(row)">处理异常</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="recordPage.page"
              v-model:page-size="recordPage.page_size"
              :total="recordPage.total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              background
              @size-change="fetchRecords"
              @current-change="fetchRecords"
            />
          </div>
        </el-tab-pane>

        <!-- Tab: 打卡时间记录 -->
        <el-tab-pane label="打卡时间记录" name="punch-time-records">
          <section class="punch-time-record-panel">
            <header class="punch-time-record-title">
              <h3>打卡时间记录</h3>
              <span>原始打卡流水，每一次上班、下班、更新打卡都会保留</span>
            </header>

            <div class="filter-row filter-row--punch-time">
              <el-date-picker
                v-model="punchTimeDateRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                value-format="YYYY-MM-DD"
                style="width: 280px"
              />
              <el-tree-select
                v-model="punchTimeDept"
                :data="deptTreeOptions"
                :props="{ label: 'name', value: 'id', children: 'children' }"
                node-key="id"
                :default-expanded-keys="deptDefaultExpandedKeys"
                placeholder="部门"
                clearable
                check-strictly
                style="width: 160px"
                @visible-change="handleDeptTreeVisibleChange"
              />
              <el-select v-model="punchTimeRuleId" clearable placeholder="规则" style="width: 160px">
                <el-option v-for="rule in rules" :key="`punch-time-rule-${rule.id}`" :label="rule.name" :value="rule.id" />
              </el-select>
              <el-input
                v-model="punchTimeKeyword"
                placeholder="搜索成员姓名/工号"
                clearable
                prefix-icon="Search"
                style="width: 200px"
                @keyup.enter="fetchPunchTimeRecords"
              />
              <el-checkbox v-model="punchTimeIncludeRecentLeft">包含90天内离职成员</el-checkbox>
              <el-button type="primary" @click="fetchPunchTimeRecords">
                <el-icon><Search /></el-icon> 查询
              </el-button>
              <el-button @click="exportPunchTimeRecords">
                <el-icon><Download /></el-icon> 导出报表
              </el-button>
              <el-button @click="resetPunchTimeFilters">重置</el-button>
            </div>

            <el-table
              :data="punchTimeRows"
              v-loading="punchTimeLoading"
              class="modern-table punch-time-calendar-table"
              row-class-name="modern-row"
              height="520"
            >
              <el-table-column label="姓名" width="160" fixed>
                <template #default="{ row }">
                  <div class="punch-time-employee">
                    <img v-if="row.photo_url" :src="row.photo_url" alt="" />
                    <span v-else class="punch-time-employee__avatar">{{ String(row.employee_name || '?').slice(0, 1) }}</span>
                    <strong>{{ row.employee_name }}</strong>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="基础信息">
                <el-table-column prop="department_name" label="部门" width="180" show-overflow-tooltip />
                <el-table-column prop="position" label="职务" width="140" />
              </el-table-column>
              <el-table-column label="打卡时间记录">
                <el-table-column
                  v-for="col in punchTimeDateColumns"
                  :key="col.date"
                  :label="punchTimeDayHeader(col)"
                  width="140"
                >
                  <template #default="{ row }">
                    <div class="punch-time-cell">
                      <template v-if="row.punch_times_by_date?.[col.date]?.length">
                        <span v-for="time in row.punch_times_by_date[col.date]" :key="`${row.employee_id}-${col.date}-${time}`">{{ time }}</span>
                      </template>
                      <span v-else class="punch-time-cell__empty">-</span>
                    </div>
                  </template>
                </el-table-column>
              </el-table-column>
            </el-table>

            <div class="pagination-wrapper">
              <el-pagination
                v-model:current-page="punchTimePage.page"
                v-model:page-size="punchTimePage.page_size"
                :total="punchTimePage.total"
                :page-sizes="[20, 50, 100]"
                layout="total, sizes, prev, pager, next"
                background
                @size-change="fetchPunchTimeRecords"
                @current-change="fetchPunchTimeRecords"
              />
            </div>
          </section>
        </el-tab-pane>

        <!-- Tab: 考勤规则 -->
        <el-tab-pane label="考勤规则" name="rules">
          <el-alert title="统一维护项目考勤规则" type="info" :closable="false" show-icon style="margin-bottom:16px">
            在这里配置上下班时间、定位 / WiFi 和照片要求；项目库直接选用已启用的规则，无需重复录入。
            修改共用规则会影响所有引用它的项目；只调整一个项目时，请复制规则后再关联。
            <router-link to="/field">前往项目库</router-link>
          </el-alert>
          <div class="filter-row">
            <el-button type="primary" @click="openRuleDialog()">
              <el-icon><Plus /></el-icon> 新增规则
            </el-button>
            <el-select v-model="ruleStatusFilter" placeholder="规则状态" clearable style="width: 140px" @change="fetchRules">
              <el-option label="启用" :value="true" />
              <el-option label="停用" :value="false" />
            </el-select>
            <el-button @click="fetchRules">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>

          <el-table :data="rules" v-loading="ruleLoading" class="modern-table" row-class-name="modern-row">
            <el-table-column prop="name" label="规则名称" min-width="180">
              <template #default="{ row }">
                <el-button link type="primary" @click="openRuleDialog(row)">{{ row.name }}</el-button>
              </template>
            </el-table-column>
            <el-table-column label="规则类型" width="140" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="light">{{ formatRuleType(row) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="priority" label="优先级" width="90" align="right" />
            <el-table-column prop="effective_date" label="生效日期" width="120" align="center" />
            <el-table-column label="适用范围" min-width="260" class-name="rule-scope-table-column">
              <template #default="{ row }">
                <div class="rule-scope-cell" :class="{ 'is-expanded': isRuleScopeExpanded(row) }">
                  <span class="rule-scope-text" :title="formatRuleScope(row)">{{ formatRuleScope(row) }}</span>
                  <button
                    v-if="ruleScopeNeedsToggle(row)"
                    class="rule-scope-toggle"
                    type="button"
                    @click.stop="toggleRuleScope(row)"
                  >
                    {{ isRuleScopeExpanded(row) ? '收起' : '展开' }}
                  </button>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="工作时间" width="160" align="center">
              <template #default="{ row }">
                {{ formatRuleWorkTime(row) }}
              </template>
            </el-table-column>
            <el-table-column label="打卡方式" min-width="170">
              <template #default="{ row }">
                <div class="check-methods">
                  <span class="check-method-chip" v-for="m in row.check_methods" :key="m">{{ m }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120" align="center">
              <template #default="{ row }">
                <el-switch
                  :model-value="!!row.is_active"
                  inline-prompt
                  active-text="启用"
                  inactive-text="停用"
                  @change="toggleRuleStatus(row, $event)"
                />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="260" fixed="right">
              <template #default="{ row }">
                <el-button type="primary" link size="small" @click="openRuleDialog(row)">编辑</el-button>
                <el-button type="success" link size="small" @click="copyRule(row)">复制</el-button>
                <el-button type="info" link size="small" @click="openRuleLogs(row)">变更历史</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab: 考勤审批 -->
        <el-tab-pane label="考勤审批" name="anomalies">
          <div class="filter-row">
            <el-select v-model="appealSourceFilter" placeholder="分类" style="width: 140px">
              <el-option label="全部" value="all" />
              <el-option label="审批申请" value="application" />
              <el-option label="打卡异常" value="anomaly" />
            </el-select>
            <el-select v-model="appealTypeFilter" placeholder="模板类型" clearable style="width: 180px">
              <el-option
                v-for="item in appealTypeOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
            <el-select v-model="appealStatusFilter" placeholder="审批状态" clearable style="width: 140px">
              <el-option label="审批中" value="pending" />
              <el-option label="已通过" value="approved" />
              <el-option label="已驳回" value="rejected" />
              <el-option label="已撤回" value="withdrawn" />
              <el-option label="待申诉" value="attendance_anomaly" />
            </el-select>
            <el-button @click="fetchAnomalies">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
            <span class="text-muted" style="font-size:13px; margin-left:8px">汇总审批申请与最近30天打卡异常</span>
          </div>
          <el-table
            :data="filteredAnomalyList"
            v-loading="anomalyLoading"
            class="modern-table clickable-table"
            row-class-name="modern-row"
            @row-click="openAppealDetail"
          >
            <el-table-column prop="employee_name" label="员工" width="120">
              <template #default="{ row }">
                <span class="text-bold">{{ row.employee_name }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="提交时间" width="150">
              <template #default="{ row }">
                {{ row.created_at || '-' }}
              </template>
            </el-table-column>
            <el-table-column prop="type_label" label="申请/异常类型" width="150">
              <template #default="{ row }">
                <el-tag :type="row.source === 'attendance_anomaly' ? 'danger' : 'primary'" size="small">
                  {{ row.type_label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="related_date" label="关联日期" width="120" />
            <el-table-column prop="punch_time" label="打卡/申请时间" width="150" show-overflow-tooltip />
            <el-table-column prop="summary" label="摘要/原因" min-width="220" show-overflow-tooltip />
            <el-table-column label="审批状态" width="110">
              <template #default="{ row }">
                <el-tag :type="approvalStatusTagType(row.status)" size="small">
                  {{ approvalStatusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="current_handler" label="当前节点/处理人" width="150" show-overflow-tooltip />
            <el-table-column label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <el-button
                  type="primary"
                  link
                  size="small"
                  @click.stop="openAppealDetail(row)"
                >查看详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- Tab: 汇总报表 -->
        <el-tab-pane label="汇总报表" name="reports">
          <el-tabs v-model="reportTab" class="report-main-tabs" @tab-change="handleReportTabChange">
            <el-tab-pane label="日报汇总" name="daily-report">
          <div class="filter-row filter-row--daily">
            <el-date-picker
              v-model="dailyDateRange"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="YYYY-MM-DD"
              style="width: 260px"
            />
            <el-select v-model="dailyStatus" clearable placeholder="异常状态" style="width: 140px">
              <el-option label="正常" value="正常" />
              <el-option label="迟到" value="迟到" />
              <el-option label="早退" value="早退" />
              <el-option label="缺卡" value="缺卡" />
              <el-option label="旷工" value="旷工" />
              <el-option label="请假" value="请假" />
              <el-option label="出差" value="出差" />
              <el-option label="异常" value="异常" />
            </el-select>
            <el-tree-select
              v-model="dailyDept"
              :data="deptTreeOptions"
              :props="{ label: 'name', value: 'id', children: 'children' }"
              node-key="id"
              :default-expanded-keys="deptDefaultExpandedKeys"
              placeholder="部门"
              clearable
              check-strictly
              style="width: 160px"
              @visible-change="handleDeptTreeVisibleChange"
            />
            <el-select v-model="dailyRuleId" clearable placeholder="规则" style="width: 160px">
              <el-option v-for="rule in rules" :key="`daily-rule-${rule.id}`" :label="rule.name" :value="rule.id" />
            </el-select>
            <el-input
              v-model="dailyKeyword"
              placeholder="搜索成员姓名/工号"
              clearable
              prefix-icon="Search"
              style="width: 200px"
              @keyup.enter="fetchDailyReport"
            />
            <el-checkbox v-model="dailyIncludeRecentLeft">包含90天内离职成员</el-checkbox>
            <el-button type="primary" @click="fetchDailyReport">
              <el-icon><Search /></el-icon> 查看考勤
            </el-button>
            <el-button @click="openDailyReportSettings">设置报表</el-button>
            <el-button @click="exportDailyReport">
              <el-icon><Download /></el-icon> 导出日报
            </el-button>
            <el-button @click="resetDailyReportFilters">重置</el-button>
          </div>

          <el-tabs v-model="dailySubTab" class="daily-report-subtabs">
            <el-tab-pane label="日报概况统计" name="overview" />
            <el-tab-pane label="日报打卡明细" name="detail" />
          </el-tabs>

          <el-table
            v-if="dailySubTab === 'overview'"
            :data="dailyOverviewRows"
            v-loading="dailyLoading"
            class="modern-table daily-report-table"
            row-class-name="modern-row"
          >
            <el-table-column
              v-for="col in visibleReportSettingColumns('dailyOverview', dailyFixedColumns)"
              :key="`daily-overview-fixed-${col.key}`"
              :prop="col.key"
              :label="reportColumnLabel(col)"
              :width="reportColumnWidth(col.key)"
              :fixed="reportColumnFixed('dailyOverview', col.key) || undefined"
              :show-overflow-tooltip="reportColumnOverflow(col.key)"
            />
            <el-table-column v-if="visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'basic').length" label="基础信息">
              <el-table-column
                v-for="col in visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'basic')"
                :key="`daily-overview-basic-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'attendance').length" label="考勤概况">
              <el-table-column
                v-for="col in visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'attendance')"
                :key="`daily-overview-attendance-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'abnormal').length" label="异常统计">
              <el-table-column
                v-for="col in visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'abnormal')"
                :key="`daily-overview-abnormal-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'outside').length" label="外出打卡">
              <el-table-column
                v-for="col in visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'outside')"
                :key="`daily-overview-outside-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'overtime').length" label="加班统计">
              <el-table-column
                v-for="col in visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'overtime')"
                :key="`daily-overview-overtime-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'leave').length" label="假勤统计">
              <el-table-column
                v-for="col in visibleReportGroupColumns('dailyOverview', dailyOverviewColumnGroups, 'leave')"
                :key="`daily-overview-leave-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
          </el-table>

          <el-table
            v-else
            :data="dailyDetailRows"
            v-loading="dailyLoading"
            class="modern-table daily-report-table"
            row-class-name="modern-row"
          >
            <el-table-column
              v-for="col in visibleReportSettingColumns('dailyDetail', dailyDetailColumns)"
              :key="`daily-detail-${col.key}`"
              :prop="col.key"
              :label="reportColumnLabel(col)"
              :width="reportColumnWidth(col.key)"
              :fixed="reportColumnFixed('dailyDetail', col.key) || undefined"
              :show-overflow-tooltip="reportColumnOverflow(col.key)"
            />
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="dailyPage.page"
              v-model:page-size="dailyPage.page_size"
              :total="dailyPage.total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              background
              @size-change="fetchDailyReport"
              @current-change="fetchDailyReport"
            />
          </div>
        </el-tab-pane>

            <el-tab-pane label="月报汇总" name="summary">
          <div class="filter-row filter-row--daily">
            <el-date-picker
              v-model="summaryDateRange"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="YYYY-MM-DD"
              style="width: 260px"
            />
            <el-select v-model="summaryStatus" clearable placeholder="打卡状态" style="width: 140px" placement="bottom-start" :teleported="false">
              <el-option label="正常" value="正常" />
              <el-option label="迟到" value="迟到" />
              <el-option label="早退" value="早退" />
              <el-option label="缺卡" value="缺卡" />
              <el-option label="旷工" value="旷工" />
              <el-option label="请假" value="请假" />
              <el-option label="出差" value="出差" />
              <el-option label="异常" value="异常" />
            </el-select>
            <el-tree-select
              v-model="summaryDept"
              :data="deptTreeOptions"
              :props="{ label: 'name', value: 'id', children: 'children' }"
              node-key="id"
              :default-expanded-keys="deptDefaultExpandedKeys"
              placeholder="部门"
              clearable
              check-strictly
              style="width: 160px"
              placement="bottom-start"
              :teleported="false"
              @visible-change="handleDeptTreeVisibleChange"
            />
            <el-select v-model="summaryRuleId" clearable placeholder="规则" style="width: 160px" placement="bottom-start" :teleported="false">
              <el-option v-for="rule in rules" :key="`summary-rule-${rule.id}`" :label="rule.name" :value="rule.id" />
            </el-select>
            <el-input
              v-model="summaryKeyword"
              placeholder="搜索成员姓名/工号"
              clearable
              prefix-icon="Search"
              style="width: 200px"
              @keyup.enter="fetchSummary()"
            />
            <el-checkbox v-model="summaryIncludeRecentLeft">包含90天内离职成员</el-checkbox>
            <el-button type="primary" :loading="summaryTaskCreating" @click="fetchSummary()">
              <el-icon><Search /></el-icon> 查看考勤
            </el-button>
            <el-button type="primary" plain>
              {{ summaryConfirmText }}
            </el-button>
            <el-button @click="openSummaryReportSettings">设置报表</el-button>
            <el-button :disabled="!canExportSummary" @click="exportSummary">
              <el-icon><Download /></el-icon> 导出月报
            </el-button>
            <el-button @click="resetSummaryReportFilters">重置</el-button>
          </div>
          <div v-if="summaryTaskVisible" class="summary-task-panel" :class="`summary-task-panel--${summaryTaskStatus}`" aria-live="polite">
            <div class="summary-task-panel__main">
              <el-tag size="small" :type="summaryTaskTagType">{{ summaryTaskStatusText }}</el-tag>
              <span class="summary-task-panel__message">
                {{ summaryTaskDisplayMessage }}
              </span>
              <span v-if="summaryTaskFromCache" class="summary-task-panel__cache">已复用缓存</span>
            </div>
            <el-progress
              v-if="summaryTaskStatus === 'pending' || summaryTaskStatus === 'running'"
              :percentage="summaryTaskProgress"
              :show-text="false"
              class="summary-task-panel__progress"
            />
            <el-button v-if="summaryTaskStatus === 'failed'" size="small" type="primary" @click="fetchSummary(true)">重新生成</el-button>
          </div>

          <el-tabs v-model="summarySubTab" class="daily-report-subtabs">
            <el-tab-pane label="月报概况统计" name="overview" />
            <el-tab-pane label="月报打卡明细" name="detail" />
          </el-tabs>

          <el-table
            v-if="summarySubTab === 'overview'"
            :data="summaryOverviewRows"
            v-loading="summaryLoading"
            class="modern-table daily-report-table"
            row-class-name="modern-row"
          >
            <el-table-column
              v-for="col in visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'fixed')"
              :key="`summary-overview-fixed-${col.key}`"
              :prop="col.key"
              :label="reportColumnLabel(col)"
              :width="reportColumnWidth(col.key)"
              :fixed="reportColumnFixed('summaryOverview', col.key) || undefined"
              :show-overflow-tooltip="reportColumnOverflow(col.key)"
            />
            <el-table-column v-if="visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'basic').length" label="基础信息">
              <el-table-column
                v-for="col in visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'basic')"
                :key="`summary-overview-basic-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'attendance').length" label="考勤概况">
              <el-table-column
                v-for="col in visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'attendance')"
                :key="`summary-overview-attendance-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'abnormal').length" label="异常统计">
              <el-table-column
                v-for="col in visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'abnormal')"
                :key="`summary-overview-abnormal-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'leave').length" label="假勤统计">
              <el-table-column
                v-for="col in visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'leave')"
                :key="`summary-overview-leave-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
            <el-table-column v-if="visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'overtime').length" label="加班统计">
              <el-table-column
                v-for="col in visibleReportGroupColumns('summaryOverview', summaryOverviewColumnGroups, 'overtime')"
                :key="`summary-overview-overtime-${col.key}`"
                :prop="col.key"
                :label="reportColumnLabel(col)"
                :width="reportColumnWidth(col.key)"
                :show-overflow-tooltip="reportColumnOverflow(col.key)"
              />
            </el-table-column>
          </el-table>

          <el-table
            v-else
            :data="summaryDetailRows"
            v-loading="summaryLoading"
            class="modern-table daily-report-table"
            row-class-name="modern-row"
          >
            <el-table-column
              v-for="col in visibleReportSettingColumns('summaryDetail', summaryDetailBaseColumns)"
              :key="`summary-detail-${col.key}`"
              :prop="col.key"
              :label="reportColumnLabel(col)"
              :width="reportColumnWidth(col.key)"
              :fixed="reportColumnFixed('summaryDetail', col.key) || undefined"
              :show-overflow-tooltip="reportColumnOverflow(col.key)"
            />
            <el-table-column
              v-for="col in summaryDayColumns"
              :key="col.key"
              :prop="col.key"
              :label="col.title"
              width="180"
              show-overflow-tooltip
            />
          </el-table>

          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="summaryPage.page"
              v-model:page-size="summaryPage.page_size"
              :total="summaryPage.total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              popper-class="summary-page-size-popper"
              background
              @size-change="handleSummaryPageChange"
              @current-change="handleSummaryPageChange"
            />
          </div>
            </el-tab-pane>
          </el-tabs>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-drawer v-model="dailyReportSettingVisible" title="设置报表" direction="rtl" size="520px" destroy-on-close class="report-setting-drawer">
      <div class="report-setting-subtitle">配置报表显示字段和顺序，拖动字段行可调整导出表头顺序。</div>
      <el-tabs v-model="dailySettingTab" class="report-setting-tabs">
        <el-tab-pane label="日报概况统计" name="overview">
          <el-collapse v-model="dailySettingOpenGroups" class="report-setting-collapse">
            <el-collapse-item v-for="group in dailyOverviewColumnGroups" :key="`setting-overview-${group.key}`" :name="group.key">
              <template #title>
                <span class="report-setting-group-title">{{ group.label }}</span>
                <span class="report-setting-group-count">{{ countVisibleReportColumns('dailyOverview', group.columns) }}/{{ group.columns.length }}</span>
              </template>
              <div class="report-field-list">
                <div
                  v-for="col in orderedReportSettingColumns('dailyOverview', group.columns)"
                  :key="`setting-overview-col-${col.key}`"
                  :class="reportFieldRowClass('dailyOverview', col.key)"
                  @dragenter.prevent="enterReportColumnDrop('dailyOverview', col.key, $event)"
                  @dragover.prevent="enterReportColumnDrop('dailyOverview', col.key, $event)"
                  @drop="dropReportColumn('dailyOverview', col.key)"
                >
                  <el-checkbox
                    :model-value="isReportColumnVisible('dailyOverview', col.key)"
                    @change="toggleReportColumn('dailyOverview', col.key, $event)"
                  >
                    {{ col.label }}
                  </el-checkbox>
                  <span
                    class="report-field-row__drag"
                    draggable="true"
                    @dragstart.stop="startReportColumnDrag('dailyOverview', col.key)"
                    @dragend.stop="clearReportColumnDrag"
                  >☰</span>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-tab-pane>
        <el-tab-pane label="日报打卡明细" name="detail">
          <div class="report-field-list">
            <div
              v-for="col in orderedReportSettingColumns('dailyDetail', dailyDetailColumns)"
              :key="`setting-detail-col-${col.key}`"
              :class="reportFieldRowClass('dailyDetail', col.key)"
              @dragenter.prevent="enterReportColumnDrop('dailyDetail', col.key, $event)"
              @dragover.prevent="enterReportColumnDrop('dailyDetail', col.key, $event)"
              @drop="dropReportColumn('dailyDetail', col.key)"
            >
              <el-checkbox
                :model-value="isReportColumnVisible('dailyDetail', col.key)"
                @change="toggleReportColumn('dailyDetail', col.key, $event)"
              >
                {{ col.label }}
              </el-checkbox>
              <span
                class="report-field-row__drag"
                draggable="true"
                @dragstart.stop="startReportColumnDrag('dailyDetail', col.key)"
                @dragend.stop="clearReportColumnDrag"
              >☰</span>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="resetDailyReportSettings">恢复默认</el-button>
        <el-button @click="dailyReportSettingVisible = false">取消</el-button>
        <el-button type="primary" @click="saveDailyReportSettings">保存</el-button>
      </template>
    </el-drawer>

    <el-drawer v-model="summaryReportSettingVisible" title="设置报表" direction="rtl" size="520px" destroy-on-close class="report-setting-drawer">
      <div class="report-setting-subtitle">配置报表显示字段和顺序，拖动字段行可调整导出表头顺序。</div>
      <el-tabs v-model="summarySettingTab" class="report-setting-tabs">
        <el-tab-pane label="月报概况统计" name="overview">
          <el-collapse v-model="summarySettingOpenGroups" class="report-setting-collapse">
            <el-collapse-item v-for="group in summaryOverviewColumnGroups" :key="`setting-summary-${group.key}`" :name="group.key">
              <template #title>
                <span class="report-setting-group-title">{{ group.label }}</span>
                <span class="report-setting-group-count">{{ countVisibleReportColumns('summaryOverview', group.columns) }}/{{ group.columns.length }}</span>
              </template>
              <div class="report-field-list">
                <div
                  v-for="col in orderedReportSettingColumns('summaryOverview', group.columns)"
                  :key="`setting-summary-col-${col.key}`"
                  :class="reportFieldRowClass('summaryOverview', col.key)"
                  @dragenter.prevent="enterReportColumnDrop('summaryOverview', col.key, $event)"
                  @dragover.prevent="enterReportColumnDrop('summaryOverview', col.key, $event)"
                  @drop="dropReportColumn('summaryOverview', col.key)"
                >
                  <el-checkbox
                    :model-value="isReportColumnVisible('summaryOverview', col.key)"
                    @change="toggleReportColumn('summaryOverview', col.key, $event)"
                  >
                    {{ col.label }}
                  </el-checkbox>
                  <span
                    class="report-field-row__drag"
                    draggable="true"
                    @dragstart.stop="startReportColumnDrag('summaryOverview', col.key)"
                    @dragend.stop="clearReportColumnDrag"
                  >☰</span>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-tab-pane>
        <el-tab-pane label="月报打卡明细" name="detail">
          <div class="daily-setting-hint">日期列会按所选时间范围自动展示，基础列可按需显隐并持久化。</div>
          <div class="report-field-list">
            <div
              v-for="col in orderedReportSettingColumns('summaryDetail', summaryDetailBaseColumns)"
              :key="`setting-summary-detail-col-${col.key}`"
              :class="reportFieldRowClass('summaryDetail', col.key)"
              @dragenter.prevent="enterReportColumnDrop('summaryDetail', col.key, $event)"
              @dragover.prevent="enterReportColumnDrop('summaryDetail', col.key, $event)"
              @drop="dropReportColumn('summaryDetail', col.key)"
            >
              <el-checkbox
                :model-value="isReportColumnVisible('summaryDetail', col.key)"
                @change="toggleReportColumn('summaryDetail', col.key, $event)"
              >
                {{ col.label }}
              </el-checkbox>
              <span
                class="report-field-row__drag"
                draggable="true"
                @dragstart.stop="startReportColumnDrag('summaryDetail', col.key)"
                @dragend.stop="clearReportColumnDrag"
              >☰</span>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="resetSummaryReportSettings">恢复默认</el-button>
        <el-button @click="summaryReportSettingVisible = false">取消</el-button>
        <el-button type="primary" @click="saveSummaryReportSettings">保存</el-button>
      </template>
    </el-drawer>

    <!-- 考勤规则对话框 -->
    <el-dialog v-model="ruleDialogVisible" :title="ruleForm.id ? '编辑考勤规则' : '新增考勤规则'" width="1120px" destroy-on-close class="modern-dialog">
      <el-form :model="ruleForm" :rules="ruleFormRules" ref="ruleFormRef" label-width="132px" class="rule-form-stack">
        <div class="rule-module">
          <div class="rule-module__title">规则设置</div>
          <el-form-item label="规则名称" prop="name">
            <el-input v-model="ruleForm.name" placeholder="请输入规则名称" />
            <span class="hint-text hint-text--danger">规则名称由1-40个中文、英文、数字及合法字符组成</span>
          </el-form-item>
          <el-form-item v-if="ruleForm.extra.scope_mode === 'project'" label="适用人员">
            <span>由项目库中的参与人员和日期决定，在这里仅配置考勤要求。</span>
          </el-form-item>
          <el-form-item v-else label="打卡人员">
            <div class="assignee-picker">
              <div class="assignee-picker__chips" v-if="(ruleForm.extra.assigned_employee_ids || []).length">
                <el-tag
                  v-for="e in selectedAssignedEmployees"
                  :key="`assigned-${e.id}`"
                  closable
                  @close="removeAssignee(e.id)"
                >
                  {{ e.name }}
                </el-tag>
              </div>
              <el-popover
                v-model:visible="assigneePickerVisible"
                trigger="click"
                placement="bottom-start"
                :width="760"
                popper-class="assignee-picker-popover"
              >
                <template #reference>
                  <el-button class="assignee-add-btn">添加</el-button>
                </template>
                <div class="assignee-tree-panel">
                  <div class="assignee-tree-panel__toolbar">
                    <el-input
                      v-model="assigneeKeyword"
                      clearable
                      placeholder="搜索部门或员工"
                      class="assignee-tree-panel__search"
                    />
                    <el-button link type="primary" @click="selectAllAssignees">全选</el-button>
                    <el-button link @click="clearAssignees">清空</el-button>
                  </div>
                  <el-tree
                    ref="assigneeTreeRef"
                    node-key="key"
                    show-checkbox
                    :default-expanded-keys="assigneeDefaultExpandedKeys"
                    :check-on-click-node="true"
                    :expand-on-click-node="false"
                    :data="assigneeTreeData"
                    :props="{ label: 'label', children: 'children' }"
                    :filter-node-method="filterAssigneeTreeNode"
                    class="assignee-tree"
                    @check="handleAssigneeTreeCheck"
                  >
                    <template #default="{ data }">
                      <span class="assignee-tree-node" :class="`assignee-tree-node--${data.type}`">
                        <span class="assignee-tree-node__badge">{{ data.type === 'company' ? '司' : data.type === 'department' ? '部' : '员' }}</span>
                        <span class="assignee-tree-node__label">{{ data.label }}</span>
                        <span v-if="data.type === 'employee' && data.employeeNo" class="assignee-tree-node__meta">({{ data.employeeNo }})</span>
                        <span v-if="data.type !== 'employee'" class="assignee-tree-node__count">{{ data.memberCount || 0 }}人</span>
                      </span>
                    </template>
                  </el-tree>
                </div>
              </el-popover>
              <div class="assignee-picker__summary">{{ assignedSummaryText }}</div>
            </div>
          </el-form-item>
          <el-form-item label="规则类型">
            <div class="rule-type-cards">
              <div
                v-for="opt in ruleTypeOptions"
                :key="opt.value"
                class="rule-type-card"
                :class="{ 'is-active': ruleForm.extra.rule_type === opt.value }"
                @click="changeRuleType(opt.value)"
              >
                <div class="rule-type-card__title">
                  {{ opt.label }}
                  <el-tag v-if="opt.value !== 'free'" size="small" effect="plain">弹性打卡</el-tag>
                </div>
                <div class="rule-type-card__desc">{{ opt.desc }}</div>
              </div>
            </div>
          </el-form-item>
          <template v-if="ruleForm.extra.rule_type === 'fixed'">
            <el-form-item label="打卡时间">
              <el-button @click="addTimeSegment(true)">添加</el-button>
              <span class="hint-text">可设置工作日、上下班时间、弹性上下班等规则</span>
            </el-form-item>
            <div class="time-table-wrap" v-if="ruleForm.extra.time_segments?.length">
              <div class="time-table">
                <div class="time-table__head">
                  <div>工作日</div>
                  <div>上下班时间</div>
                </div>
                <div
                  v-for="(segment, idx) in ruleForm.extra.time_segments"
                  :key="segment._key || idx"
                  class="time-table__row"
                >
                  <div class="time-table__weekday">{{ formatSegmentWeekdays(segment) }}</div>
                  <div class="time-table__detail">
                    <div class="time-table__line">
                      <span class="time-table__label">工作时间</span>
                      <span>{{ segment.clock_in || '--:--' }}({{ segment.check_in_required ? '需打卡' : '无需打卡' }}) - {{ segment.clock_out || '--:--' }}({{ segment.check_out_required ? '需打卡' : '无需打卡' }})</span>
                    </div>
                    <div class="time-table__line"><span class="time-table__label">休息时间</span><span>{{ segment.rest_start || '--:--' }} - {{ segment.rest_end || '--:--' }}</span></div>
                    <div class="time-table__line"><span class="time-table__label">弹性打卡</span><span>{{ flexModeLabel(segment.flex_mode || 'none') }}</span></div>
                    <div class="time-table__line"><span class="time-table__label">可打卡时段</span><span>{{ formatPunchWindow(segment.punch_start, segment.punch_end) }}</span></div>
                    <div class="time-table__line"><span class="time-table__label">半天工作时间</span><span>上午 {{ (segment.half_day_am || [])[0] || '--:--' }}-{{ (segment.half_day_am || [])[1] || '--:--' }}，下午 {{ (segment.half_day_pm || [])[0] || '--:--' }}-{{ (segment.half_day_pm || [])[1] || '--:--' }}</span></div>
                  </div>
                  <div class="time-table__actions">
                    <el-button type="primary" link @click="openSegmentEditor(segment, idx)">编辑</el-button>
                    <el-button type="primary" link class="time-table__more">⋮</el-button>
                    <el-button v-if="ruleForm.extra.time_segments.length > 1" type="danger" link @click="removeTimeSegment(idx)">删除</el-button>
                  </div>
                </div>
                <div class="time-table__foot">
                  <el-button link type="primary" @click="addTimeSegment(true)"><el-icon><Plus /></el-icon> 添加</el-button>
                  <span>适用于一周多种工作时间</span>
                </div>
              </div>
            </div>
          </template>
          <template v-else-if="ruleForm.extra.rule_type === 'free'">
            <el-form-item label="打卡时间">
              <div class="free-time-panel">
                <div class="free-time-row">
                  <div class="free-time-label"><span class="required-star">*</span>工作日</div>
                  <el-checkbox-group v-model="ruleForm.extra.free_workdays">
                    <el-checkbox v-for="w in weekdayOptions" :key="`free-wd-${w.value}`" :value="w.value">{{ w.label }}</el-checkbox>
                  </el-checkbox-group>
                </div>
                <div class="free-time-row">
                  <div class="free-time-label">工作时长</div>
                  <el-select v-model="ruleForm.extra.free_work_hours_mode" style="width: 150px">
                    <el-option label="不限制" value="unlimited" />
                    <el-option v-for="h in Array.from({ length: 24 }, (_, i) => i + 1)" :key="`free-hour-${h}`" :label="`${h}小时`" :value="`limit:${h}`" />
                  </el-select>
                  <span class="free-time-tip">少于所选工作时长将记为异常；超过部分按加班规则计算。选择「不限制」则不校验最低工时。</span>
                </div>
                <div class="free-time-row">
                  <div class="free-time-label">打卡开始时间</div>
                  <span>每天</span>
                  <el-time-picker v-model="ruleForm.extra.free_day_start_time" format="HH:mm" value-format="HH:mm" style="width: 140px" />
                  <span>开始新的一天打卡</span>
                </div>
              </div>
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item label="排班设置">
              <el-button v-if="ruleForm.extra.scope_mode !== 'project'" @click="openShiftArrangeDialog">编辑排班</el-button>
              <template v-else><el-button @click="openShiftClassDrawer">设置班次</el-button><span>项目使用第一个工作班次；不同班次请分别配置规则。人员和日期在项目库安排。</span></template>
            </el-form-item>
            <div class="shift-setting-summary">
              <div><span>排班方式</span><strong>{{ shiftArrangeModeText }}</strong></div>
              <div><span>打卡班次</span><strong>{{ shiftTemplateSummaryText }}</strong></div>
              <div><span>更多设置</span><strong>{{ shiftSettingMoreText }}</strong></div>
            </div>
          </template>
          <el-form-item label="特殊日期">
            <el-button v-if="ruleForm.extra.rule_type === 'fixed'" @click="openSpecialDateDialog('must')">添加必须打卡日期</el-button>
            <el-button @click="openSpecialDateDialog('no')">添加无需打卡日期</el-button>
          </el-form-item>
          <div
            v-if="(ruleForm.extra.special_must_dates || []).length || (ruleForm.extra.special_no_dates || []).length"
            class="special-date-inline"
          >
            <div class="special-date-inline__block">
              <div class="special-date-inline__title">必须打卡日期</div>
              <div v-if="!(ruleForm.extra.special_must_dates || []).length" class="hint-text">暂未设置</div>
              <div v-else class="special-date-list">
                <div
                  v-for="(d, idx) in (ruleForm.extra.special_must_dates || [])"
                  :key="`must-${idx}`"
                  class="special-date-item"
                >
                  <span>{{ summarizeSpecialDate(d) }}</span>
                  <el-button type="danger" link @click="removeSpecialDate('must', idx)">删除</el-button>
                </div>
              </div>
            </div>
            <div class="special-date-inline__block">
              <div class="special-date-inline__title">无需打卡日期</div>
              <div v-if="!(ruleForm.extra.special_no_dates || []).length" class="hint-text">暂未设置</div>
              <div v-else class="special-date-list">
                <div
                  v-for="(d, idx) in (ruleForm.extra.special_no_dates || [])"
                  :key="`no-${idx}`"
                  class="special-date-item"
                >
                  <span>{{ summarizeSpecialDate(d) }}</span>
                  <el-button type="danger" link @click="removeSpecialDate('no', idx)">删除</el-button>
                </div>
              </div>
            </div>
          </div>
          <el-form-item label="节假日">
            <el-checkbox v-model="ruleForm.extra.legal_holiday_no_punch">中国法定节假日不用打卡</el-checkbox>
            <span class="holiday-desc">法定节假日及调休放假日不用打卡，补班日期按正常上班时间打卡</span>
            <el-button link type="primary" class="hint-link" @click="openHolidayCalendar">查看法定节假日日历</el-button>
          </el-form-item>
          <el-form-item :label="ruleForm.extra.rule_type === 'free' ? '打卡设置' : '休息日'">
            <el-button @click="openRestdaySettingDialog">设置</el-button>
            <span class="restday-desc">{{ restdaySummaryText }}</span>
          </el-form-item>
        </div>

        <div class="rule-module">
          <div class="rule-module__title"><span class="required-star">*</span>打卡方式</div>
          <div class="hint-text punch-method-tip">手机和考勤机满足任意一项即可打卡</div>
          <el-form-item>
            <template #label>
              <span class="field-label-with-tip"><span class="required-star">*</span>选择方式<el-icon class="field-info-icon"><QuestionFilled /></el-icon></span>
            </template>
            <el-radio-group v-model="ruleForm.extra.punch_choice_mode" class="punch-choice-group">
              <div class="punch-choice-item">
                <el-radio value="mobile">手机</el-radio>
                <span class="punch-choice-item__desc">可通过位置和WiFi任意一项打卡</span>
              </div>
              <div class="punch-choice-item">
                <el-radio value="device">考勤机</el-radio>
                <span class="punch-choice-item__desc">在考勤机上打卡，在企业微信查看记录</span>
              </div>
              <div class="punch-choice-item">
                <el-radio value="both">手机+考勤机</el-radio>
                <span class="punch-choice-item__desc">需满足手机（位置或WiFi）与考勤机打卡</span>
              </div>
            </el-radio-group>
          </el-form-item>
          <el-form-item>
            <template #label>
              <span class="field-label-with-tip">打卡位置<el-icon class="field-info-icon"><QuestionFilled /></el-icon></span>
            </template>
            <el-button @click="openLocationDialog">添加</el-button>
            <div class="popup-manage__items">
              <el-tag v-for="(loc, idx) in ruleForm.extra.locations" :key="`loc-${idx}`" closable @close="removeLocation(idx)">{{ loc.name }} {{ loc.radius }}米</el-tag>
            </div>
          </el-form-item>
          <el-form-item>
            <template #label>
              <span class="field-label-with-tip">打卡Wi-Fi<el-icon class="field-info-icon"><QuestionFilled /></el-icon></span>
            </template>
            <el-button @click="openWifiDialog">添加</el-button>
            <div class="popup-manage__items">
              <el-tag v-for="(wifi, idx) in ruleForm.extra.wifi_list" :key="`wifi-${idx}`" type="success" closable @close="removeWifi(idx)">{{ wifi.name }}</el-tag>
            </div>
          </el-form-item>
        </div>

        <div class="rule-module">
          <div class="rule-module__title">加班规则</div>
          <div class="overtime-setting-block">
            <div class="overtime-setting-row">
              <div class="overtime-setting-row__label">加班规则</div>
              <div class="overtime-setting-row__value">
                <div class="overtime-setting-row__action">
                  <el-switch v-model="ruleForm.extra.enable_overtime" />
                  <el-button :disabled="!ruleForm.extra.enable_overtime" @click="openOvertimeRuleDialog">设置</el-button>
                </div>
                <div class="rule-summary-card">
                  <div>工作日　{{ ruleForm.extra.overtime_rule_workday }}</div>
                  <div>休息日　{{ ruleForm.extra.overtime_rule_restday }}</div>
                  <div>节假日　{{ ruleForm.extra.overtime_rule_holiday }}</div>
                </div>
              </div>
            </div>
            <div class="overtime-setting-row">
              <div class="overtime-setting-row__label">加班时长设置</div>
              <div class="overtime-setting-row__value">
                <div class="overtime-setting-row__action">
                  <el-button :disabled="!ruleForm.extra.enable_overtime" @click="openOvertimeDurationDialog">设置</el-button>
                </div>
                <div class="rule-summary-card">
                  <div>加班单位　{{ ruleForm.extra.overtime_unit }}</div>
                  <div>取整方式　{{ ruleForm.extra.overtime_rounding_rule }}</div>
                  <div>单位换算　{{ ruleForm.extra.overtime_unit_convert }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="rule-module">
          <div class="rule-module__title">请假时打卡设置</div>
          <div class="attendance-setting-tip">
            补卡、请假、加班等审批模板统一在「审批管理」维护。
            <router-link to="/approval">前往审批管理</router-link>
          </div>

          <div class="attendance-setting-row">
            <div class="attendance-setting-row__label">
              审批打卡
              <el-icon class="attendance-setting-row__info"><QuestionFilled /></el-icon>
            </div>
            <div class="attendance-setting-row__content">
              <div class="attendance-setting-row__main">
                <el-checkbox v-model="ruleForm.extra.enable_approve_punch">开启</el-checkbox>
                <span class="attendance-setting-row__desc">定位不准等原因无法打卡时，可提交审批打卡</span>
              </div>
            </div>
          </div>

          <div class="attendance-setting-row">
            <div class="attendance-setting-row__label">
              补卡申请
              <el-icon class="attendance-setting-row__info"><QuestionFilled /></el-icon>
            </div>
            <div class="attendance-setting-row__content">
              <div class="attendance-setting-row__main attendance-setting-row__main--wrap">
                <el-switch v-model="ruleForm.extra.enable_patch_apply" />
                <span class="attendance-setting-row__desc">开启后，命中本规则的成员可在移动端提交缺卡、迟到、早退等补卡审批</span>
              </div>
              <div v-if="ruleForm.extra.enable_patch_apply" class="attendance-patch-card">
                <div class="attendance-patch-card__line attendance-patch-card__line--types">
                  <span class="attendance-patch-card__line-label">可补类型</span>
                  <el-checkbox-group v-model="ruleForm.extra.patch_types" class="attendance-patch-card__types">
                    <el-checkbox v-for="item in patchTypeOptions" :key="item" :label="item" />
                  </el-checkbox-group>
                </div>
                <div class="attendance-patch-card__line">
                  <span class="attendance-patch-card__line-label">补卡日期范围</span>
                  <el-select v-model="ruleForm.extra.patch_time_limit" class="attendance-setting-select">
                    <el-option v-for="item in patchTimeLimitOptions" :key="item" :label="item" :value="item" />
                  </el-select>
                  <span class="attendance-patch-card__line-tip">限制员工可提交的目标补卡日期</span>
                </div>
                <div class="attendance-patch-card__line">
                  <span class="attendance-patch-card__line-label">每月补卡次数</span>
                  <el-select v-model="ruleForm.extra.patch_month_limit" class="attendance-setting-select">
                    <el-option v-for="item in patchMonthLimitOptions" :key="item" :label="item" :value="item" />
                  </el-select>
                  <span class="attendance-patch-card__line-tip">按员工、补卡目标月份统计待审批和已通过补卡</span>
                </div>
                <div class="attendance-patch-card__line">
                  <span class="attendance-patch-card__line-label">月度截止日</span>
                  <el-select v-model="ruleForm.extra.patch_deadline" class="attendance-setting-select">
                    <el-option v-for="item in patchDeadlineOptions" :key="item" :label="item" :value="item" />
                  </el-select>
                  <span class="attendance-patch-card__line-tip">超过截止日后，不再允许补上月卡点</span>
                </div>
              </div>
            </div>
          </div>

          <div class="attendance-setting-row">
            <div class="attendance-setting-row__label">请假时打卡</div>
            <div class="attendance-setting-row__content">
              <div class="attendance-setting-row__main">
                <el-button @click="openLeavePunchSettingDialog">设置</el-button>
                <span class="attendance-setting-row__desc">可设置成员按半天/按小时请假时，需在离岗前和返岗后打卡</span>
              </div>
              <div class="attendance-setting-summary">
                <span class="attendance-setting-summary__title">离岗返岗打卡</span>
                <span class="attendance-setting-summary__value">{{ leavePunchSummaryText }}</span>
              </div>
            </div>
          </div>

          <div class="attendance-setting-row">
            <div class="attendance-setting-row__label">补交审批限制</div>
            <div class="attendance-setting-row__content">
              <div class="attendance-setting-row__main attendance-setting-row__main--wrap">
                <el-switch v-model="ruleForm.extra.leave_retroactive_limit_enabled" />
                <span class="attendance-setting-row__desc">限制历史请假补交，避免已结算月份被重新改动</span>
              </div>
              <div v-if="ruleForm.extra.leave_retroactive_limit_enabled" class="retroactive-setting-grid">
                <label>
                  <span>允许补交</span>
                  <el-input-number v-model="ruleForm.extra.leave_retroactive_limit_months" :min="0" :max="12" :step="1" controls-position="right" />
                  <span>个月内</span>
                </label>
                <label>
                  <span>每月</span>
                  <el-input-number v-model="ruleForm.extra.leave_retroactive_cutoff_day" :min="1" :max="31" :step="1" controls-position="right" />
                  <span>日后禁止补交上月及更早</span>
                </label>
                <el-checkbox v-model="ruleForm.extra.leave_retroactive_block_locked_summary">考勤月报锁定后禁止补交</el-checkbox>
              </div>
              <div class="attendance-setting-summary">
                <span class="attendance-setting-summary__title">工资口径</span>
                <span class="attendance-setting-summary__value">历史假期审批通过月生成调整，不回写已锁定月报</span>
              </div>
            </div>
          </div>
        </div>

        <div class="rule-module">
          <div class="rule-module__title">更多设置</div>
          <el-form-item>
            <template #label>
              <span><span class="required-star">*</span>时区</span>
            </template>
            <div class="more-setting-line">
              <el-select v-model="ruleForm.extra.timezone" style="width: 360px" :disabled="!!ruleForm.id">
                <el-option v-for="item in timezoneOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
              <div class="hint-text">成员将按照设置的时区进行打卡和统计，保存后不支持修改</div>
            </div>
          </el-form-item>

          <el-form-item>
            <template #label>
              <span><span class="required-star">*</span>手机提醒<el-icon class="field-info-icon"><QuestionFilled /></el-icon></span>
            </template>
            <div class="more-setting-remind">
              <span>上班</span>
              <el-select v-model="ruleForm.extra.mobile_remind_on_duty" style="width: 140px">
                <el-option v-for="item in mobileRemindOnDutyOptions" :key="item" :label="item" :value="item" />
              </el-select>
              <span>—</span>
              <span>下班</span>
              <el-select v-model="ruleForm.extra.mobile_remind_off_duty" style="width: 120px">
                <el-option v-for="item in mobileRemindOffDutyOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </div>
          </el-form-item>

          <el-form-item label="拍照与人脸识别">
            <div class="more-setting-line">
              <el-checkbox v-model="ruleForm.extra.photo_each_punch">每次打卡均需拍照</el-checkbox>
              <el-checkbox v-model="ruleForm.extra.watermark_photo_required">外出打卡必须水印拍照</el-checkbox>
              <el-checkbox v-model="ruleForm.extra.face_each_punch">每次打卡均需人脸识别 <el-icon class="field-info-icon"><QuestionFilled /></el-icon></el-checkbox>
              <el-checkbox v-model="ruleForm.extra.note_photo_only">备注图片只能拍照 <el-icon class="field-info-icon"><QuestionFilled /></el-icon></el-checkbox>
            </div>
          </el-form-item>

          <el-form-item label="范围外打卡">
            <el-radio-group v-model="ruleForm.extra.outside_punch_mode" class="outside-punch-mode-group">
              <el-radio value="abnormal">允许范围外打卡，记录为地点异常</el-radio>
              <el-radio value="fieldwork">允许范围外打卡，记录为正常外勤</el-radio>
              <el-radio value="forbidden">不允许范围外打卡　若开启了审批打卡，成员仍可打卡。可在规则中修改配置</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="外出打卡记录同步">
            <div class="more-setting-remind">
              <el-checkbox v-model="ruleForm.extra.outside_record_sync">开启</el-checkbox>
              <span class="hint-text">开启后，员工在上下班期间外出打卡记录将同步至上下班中，结果显示为「外出打卡」</span>
            </div>
          </el-form-item>

          <el-form-item>
            <template #label>
              <span>无需打卡人员<el-icon class="field-info-icon"><QuestionFilled /></el-icon></span>
            </template>
            <div class="more-setting-picker">
              <el-popover
                v-model:visible="noPunchSelectorVisible"
                trigger="click"
                placement="bottom-start"
                :width="760"
                popper-class="assignee-picker-popover"
              >
                <template #reference>
                  <el-button>添加</el-button>
                </template>
                <div class="assignee-tree-panel">
                  <div class="assignee-tree-panel__toolbar">
                    <el-input v-model="noPunchKeyword" clearable placeholder="搜索部门或员工" class="assignee-tree-panel__search" />
                    <el-button link type="primary" @click="selectAllMemberPicker('no_punch')">全选</el-button>
                    <el-button link @click="clearMemberPicker('no_punch')">清空</el-button>
                  </div>
                  <el-tree
                    ref="noPunchTreeRef"
                    node-key="key"
                    show-checkbox
                    :default-expanded-keys="assigneeDefaultExpandedKeys"
                    :check-on-click-node="true"
                    :expand-on-click-node="false"
                    :data="assigneeTreeData"
                    :props="{ label: 'label', children: 'children' }"
                    :filter-node-method="filterAssigneeTreeNode"
                    class="assignee-tree"
                    @check="handleMemberPickerTreeCheck('no_punch')"
                  >
                    <template #default="{ data }">
                      <span class="assignee-tree-node" :class="`assignee-tree-node--${data.type}`">
                        <span class="assignee-tree-node__badge">{{ data.type === 'company' ? '司' : data.type === 'department' ? '部' : '员' }}</span>
                        <span class="assignee-tree-node__label">{{ data.label }}</span>
                        <span v-if="data.type === 'employee' && data.employeeNo" class="assignee-tree-node__meta">({{ data.employeeNo }})</span>
                        <span v-if="data.type !== 'employee'" class="assignee-tree-node__count">{{ data.memberCount || 0 }}人</span>
                      </span>
                    </template>
                  </el-tree>
                </div>
              </el-popover>
              <div class="more-setting-picker__selected">
                <el-tag
                  v-for="e in selectedNoPunchEmployees"
                  :key="`np-selected-${e.id}`"
                  closable
                  @close="removeMemberPickerEmployee('no_punch', e.id)"
                >{{ employeeDisplayName(e) }}</el-tag>
                <span v-if="!selectedNoPunchEmployees.length" class="more-setting-picker__placeholder">未选择无需打卡人员</span>
              </div>
            </div>
          </el-form-item>

          <el-form-item>
            <template #label>
              <span>汇报对象<el-icon class="field-info-icon"><QuestionFilled /></el-icon></span>
            </template>
            <div class="more-setting-picker">
              <el-popover
                v-model:visible="reportTargetSelectorVisible"
                trigger="click"
                placement="bottom-start"
                :width="760"
                popper-class="assignee-picker-popover"
              >
                <template #reference>
                  <el-button>添加</el-button>
                </template>
                <div class="assignee-tree-panel">
                  <div class="assignee-tree-panel__toolbar">
                    <el-input v-model="reportTargetKeyword" clearable placeholder="搜索部门或员工" class="assignee-tree-panel__search" />
                    <el-button link type="primary" @click="selectAllMemberPicker('report_target')">全选</el-button>
                    <el-button link @click="clearMemberPicker('report_target')">清空</el-button>
                  </div>
                  <el-tree
                    ref="reportTargetTreeRef"
                    node-key="key"
                    show-checkbox
                    :default-expanded-keys="assigneeDefaultExpandedKeys"
                    :check-on-click-node="true"
                    :expand-on-click-node="false"
                    :data="assigneeTreeData"
                    :props="{ label: 'label', children: 'children' }"
                    :filter-node-method="filterAssigneeTreeNode"
                    class="assignee-tree"
                    @check="handleMemberPickerTreeCheck('report_target')"
                  >
                    <template #default="{ data }">
                      <span class="assignee-tree-node" :class="`assignee-tree-node--${data.type}`">
                        <span class="assignee-tree-node__badge">{{ data.type === 'company' ? '司' : data.type === 'department' ? '部' : '员' }}</span>
                        <span class="assignee-tree-node__label">{{ data.label }}</span>
                        <span v-if="data.type === 'employee' && data.employeeNo" class="assignee-tree-node__meta">({{ data.employeeNo }})</span>
                        <span v-if="data.type !== 'employee'" class="assignee-tree-node__count">{{ data.memberCount || 0 }}人</span>
                      </span>
                    </template>
                  </el-tree>
                </div>
              </el-popover>
              <div class="more-setting-picker__selected">
                <el-tag
                  v-for="e in selectedReportTargetEmployees"
                  :key="`rt-selected-${e.id}`"
                  closable
                  @close="removeMemberPickerEmployee('report_target', e.id)"
                >{{ employeeDisplayName(e) }}</el-tag>
                <span v-if="!selectedReportTargetEmployees.length" class="more-setting-picker__placeholder">未选择汇报对象</span>
              </div>
            </div>
          </el-form-item>

          <el-form-item>
            <template #label>
              <span>协助管理<el-icon class="field-info-icon"><QuestionFilled /></el-icon></span>
            </template>
            <div class="more-setting-picker">
              <el-popover
                v-model:visible="assistManagerSelectorVisible"
                trigger="click"
                placement="bottom-start"
                :width="760"
                popper-class="assignee-picker-popover"
              >
                <template #reference>
                  <el-button>设置</el-button>
                </template>
                <div class="assignee-tree-panel">
                  <div class="assignee-tree-panel__toolbar">
                    <el-input v-model="assistManagerKeyword" clearable placeholder="搜索部门或员工" class="assignee-tree-panel__search" />
                    <el-button link type="primary" @click="selectAllMemberPicker('assist_manager')">全选</el-button>
                    <el-button link @click="clearMemberPicker('assist_manager')">清空</el-button>
                  </div>
                  <el-tree
                    ref="assistManagerTreeRef"
                    node-key="key"
                    show-checkbox
                    :default-expanded-keys="assigneeDefaultExpandedKeys"
                    :check-on-click-node="true"
                    :expand-on-click-node="false"
                    :data="assigneeTreeData"
                    :props="{ label: 'label', children: 'children' }"
                    :filter-node-method="filterAssigneeTreeNode"
                    class="assignee-tree"
                    @check="handleMemberPickerTreeCheck('assist_manager')"
                  >
                    <template #default="{ data }">
                      <span class="assignee-tree-node" :class="`assignee-tree-node--${data.type}`">
                        <span class="assignee-tree-node__badge">{{ data.type === 'company' ? '司' : data.type === 'department' ? '部' : '员' }}</span>
                        <span class="assignee-tree-node__label">{{ data.label }}</span>
                        <span v-if="data.type === 'employee' && data.employeeNo" class="assignee-tree-node__meta">({{ data.employeeNo }})</span>
                        <span v-if="data.type !== 'employee'" class="assignee-tree-node__count">{{ data.memberCount || 0 }}人</span>
                      </span>
                    </template>
                  </el-tree>
                </div>
              </el-popover>
              <div class="more-setting-picker__selected">
                <el-tag
                  v-for="e in selectedAssistManagerEmployees"
                  :key="`am-selected-${e.id}`"
                  closable
                  @close="removeMemberPickerEmployee('assist_manager', e.id)"
                >{{ employeeDisplayName(e) }}</el-tag>
                <span v-if="!selectedAssistManagerEmployees.length" class="more-setting-picker__placeholder">未选择协助管理人员</span>
              </div>
            </div>
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="ruleSubmitting" @click="submitRule">{{ ruleForm.id ? '更新规则' : '确定' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="segmentEditorVisible" title="打卡时间" width="980px" class="modern-dialog segment-editor-dialog" append-to-body>
      <div class="segment-editor">
        <div class="segment-row">
          <div class="segment-label"><span class="required-star">*</span>工作日</div>
          <div class="segment-value segment-panel">
            <template v-if="!segmentDraft.biweekly_enabled">
              <el-checkbox-group v-model="segmentDraft.weekdays">
                <el-checkbox v-for="w in weekdayOptions" :key="w.value" :value="w.value">{{ w.label }}</el-checkbox>
              </el-checkbox-group>
              <div class="segment-divider"></div>
              <div class="segment-tip">大小周、上一休一可选 <el-button link type="primary" @click="enableBiweeklyForDraft">双周重复</el-button></div>
            </template>
            <template v-else>
              <div class="biweekly-row">
                <span class="biweekly-row__label">本周:</span>
                <el-checkbox-group v-model="segmentDraft.this_week_weekdays">
                  <el-checkbox v-for="w in weekdayOptions" :key="`this-${w.value}`" :value="w.value">{{ w.label }}</el-checkbox>
                </el-checkbox-group>
              </div>
              <div class="biweekly-row">
                <span class="biweekly-row__label">下周:</span>
                <el-checkbox-group v-model="segmentDraft.next_week_weekdays">
                  <el-checkbox v-for="w in weekdayOptions" :key="`next-${w.value}`" :value="w.value">{{ w.label }}</el-checkbox>
                </el-checkbox-group>
              </div>
              <div class="segment-tip segment-tip--between">
                <el-button link type="primary" @click="disableBiweeklyForDraft">取消双周重复</el-button>
                <el-popover v-model:visible="workCalendarPreviewVisible" trigger="click" placement="bottom-end" :width="350">
                  <template #reference>
                    <el-button link type="primary">工作日历预览</el-button>
                  </template>
                  <div class="calendar-preview-grid">
                    <div v-for="head in ['周一', '周二', '周三', '周四', '周五', '周六', '周日']" :key="`preview-${head}`" class="calendar-head">{{ head }}</div>
                    <div
                      v-for="cell in workCalendarCells"
                      :key="`preview-cell-${cell.key}`"
                      class="calendar-cell"
                      :class="{ 'is-muted': !cell.inMonth, 'is-workday': cell.isWorkday }"
                    >
                      {{ cell.day }}
                    </div>
                  </div>
                </el-popover>
              </div>
            </template>
          </div>
        </div>

        <div class="segment-row">
          <div class="segment-label"><span class="required-star">*</span>工作时间</div>
          <div class="segment-value segment-panel">
            <div class="segment-time-line">
              <div class="segment-time-title">上班时间</div>
              <el-time-picker v-model="segmentDraft.clock_in" format="HH:mm" value-format="HH:mm" style="width: 280px" />
              <el-select v-model="segmentDraft.check_in_required" style="width: 140px">
                <el-option label="需打卡" :value="true" />
                <el-option label="无需打卡" :value="false" />
              </el-select>
            </div>
            <div class="segment-time-line">
              <div class="segment-time-title">下班时间</div>
              <el-time-picker v-model="segmentDraft.clock_out" format="HH:mm" value-format="HH:mm" style="width: 280px" />
              <el-select v-model="segmentDraft.check_out_required" style="width: 140px">
                <el-option label="需打卡" :value="true" />
                <el-option label="无需打卡" :value="false" />
              </el-select>
            </div>
            <div v-for="(period, pIdx) in (segmentDraft.extra_periods || [])" :key="`period-${pIdx}`" class="segment-extra-period">
              <div class="segment-time-line">
                <div class="segment-time-title">上班时间</div>
                <el-time-picker v-model="period.clock_in" format="HH:mm" value-format="HH:mm" style="width: 280px" />
                <el-select v-model="period.check_in_required" style="width: 140px">
                  <el-option label="需打卡" :value="true" />
                  <el-option label="无需打卡" :value="false" />
                </el-select>
              </div>
              <div class="segment-time-line">
                <div class="segment-time-title">下班时间</div>
                <el-time-picker v-model="period.clock_out" format="HH:mm" value-format="HH:mm" style="width: 280px" />
                <el-select v-model="period.check_out_required" style="width: 140px">
                  <el-option label="需打卡" :value="true" />
                  <el-option label="无需打卡" :value="false" />
                </el-select>
              </div>
              <div class="segment-extra-period__action">
                <el-button type="danger" link @click="removeWorkPeriodFromEditor(pIdx)">删除时段</el-button>
              </div>
            </div>
            <div class="segment-time-line segment-time-line--rest">
              <div class="segment-time-title">休息时间</div>
              <template v-if="segmentDraft.rest_start && segmentDraft.rest_end">
                <span>{{ (segmentDraft.rest_periods || []).length > 1 ? `${segmentDraft.rest_start}-${segmentDraft.rest_end} 等${(segmentDraft.rest_periods || []).length}段` : `${segmentDraft.rest_start}-${segmentDraft.rest_end}` }}</span>
                <el-button link type="primary" @click="openRestTimeDialog">修改</el-button>
              </template>
              <template v-else>
                <el-button link type="primary" @click="openRestTimeDialog">添加</el-button>
              </template>
              <span class="segment-tip-muted">休息时间不计入工作时长</span>
            </div>
          </div>
        </div>

        <div class="segment-row">
          <div class="segment-label"></div>
          <div class="segment-value">
            <el-button @click="addWorkPeriodFromEditor"><el-icon><Plus /></el-icon> 添加时段</el-button>
            <span class="segment-tip-muted">适用于一天需多次上下班打卡</span>
          </div>
        </div>

        <div class="segment-row">
          <div class="segment-label">可打卡时段</div>
          <div class="segment-value">
            <span>{{ formatPunchWindow(segmentDraft.punch_start, segmentDraft.punch_end) }}</span>
            <el-button link type="primary" @click="openPunchWindowDialog">修改</el-button>
          </div>
        </div>

        <div class="segment-row">
          <div class="segment-label">弹性打卡</div>
          <div class="segment-value">
            <el-radio-group v-model="segmentDraft.flex_mode">
              <el-radio value="none">不允许</el-radio>
              <el-radio value="late_leave">允许晚到早走</el-radio>
              <el-radio value="both">允许早到早走、晚到晚走</el-radio>
            </el-radio-group>
          </div>
        </div>

        <div class="segment-row">
          <div class="segment-label">晚走晚到</div>
          <div class="segment-value">
            <el-checkbox v-model="segmentDraft.late_leave_next_day">下班晚走，次日可晚到</el-checkbox>
          </div>
        </div>

        <div class="segment-row">
          <div class="segment-label">半天工作时间</div>
          <div class="segment-value">
            <span>上午 {{ (segmentDraft.half_day_am || [])[0] || '--:--' }}-{{ (segmentDraft.half_day_am || [])[1] || '--:--' }}，下午 {{ (segmentDraft.half_day_pm || [])[0] || '--:--' }}-{{ (segmentDraft.half_day_pm || [])[1] || '--:--' }}</span>
            <el-button link type="primary" @click="openHalfDayDialog">修改</el-button>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="segment-footer">
          <el-button link type="primary" @click="copyFromExistingRule">从已有规则复制</el-button>
          <div>
            <el-button type="primary" @click="saveSegmentEditor">保存</el-button>
            <el-button @click="segmentEditorVisible = false">取消</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="restTimeDialogVisible" title="添加休息时间" width="760px" class="modern-dialog" append-to-body>
      <div class="segment-tip" style="margin-bottom: 10px;">可在工作时间段 ({{ segmentDraft.clock_in }}-{{ segmentDraft.clock_out }}) 设置多段休息时间，休息时间不计入工时</div>
      <div class="rest-time-list">
        <div v-for="(item, idx) in restTimeDraft" :key="`rest-${idx}`" class="rest-time-row">
          <div class="rest-time-row__label">休息时间{{ idx + 1 }}</div>
          <el-time-picker v-model="item.start" format="HH:mm" value-format="HH:mm" placeholder="请选择开始时间" />
          <span>-</span>
          <el-time-picker v-model="item.end" format="HH:mm" value-format="HH:mm" placeholder="请选择结束时间" />
          <div class="rest-time-row__ops">
            <el-button circle @click="removeRestPeriod(idx)" :disabled="restTimeDraft.length === 1"><el-icon><Minus /></el-icon></el-button>
            <el-button circle @click="addRestPeriod"><el-icon><Plus /></el-icon></el-button>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="restTimeDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveRestTime">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="punchWindowDialogVisible" title="可打卡时段" width="760px" class="modern-dialog" append-to-body>
      <div class="punch-window-list">
        <div v-for="(row, idx) in punchWindowDraft" :key="`pwin-${idx}`" class="punch-window-section">
          <div class="punch-window-section__title">{{ row.title }}</div>
          <div class="punch-window-row">
            <div class="punch-window-row__label">上班({{ row.clock_in }})可打卡时段</div>
            <el-time-picker v-model="row.in_start" format="HH:mm" value-format="HH:mm" />
            <span>-</span>
            <el-time-picker v-model="row.in_end" :format="isNextDayRange(row.in_start, row.in_end) ? '[次日]HH:mm' : 'HH:mm'" value-format="HH:mm" />
          </div>
          <div class="punch-window-row__hint">
            {{ getInPunchHint(row) }}
          </div>
          <div class="punch-window-row">
            <div class="punch-window-row__label">下班({{ row.clock_out }})可打卡时段</div>
            <el-time-picker v-model="row.out_start" format="HH:mm" value-format="HH:mm" />
            <span>-</span>
            <el-time-picker v-model="row.out_end" :format="isNextDayRange(row.out_start, row.out_end) ? '[次日]HH:mm' : 'HH:mm'" value-format="HH:mm" />
          </div>
          <div class="punch-window-row__hint">
            {{ getOutPunchHint(row) }}
          </div>
          <div class="punch-window-row__remove" v-if="row.removable">
            <el-button circle @click="removePunchWindowRow(idx)"><el-icon><Close /></el-icon></el-button>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="punchWindowDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="savePunchWindow">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="halfDayDialogVisible" title="半天工作时间" width="720px" class="modern-dialog" append-to-body>
      <div class="segment-tip" style="margin-bottom: 12px;">成员工作日（{{ segmentDraft.clock_in }}-{{ segmentDraft.clock_out }}）请假半天时，工作时间为：</div>
      <div class="segment-panel">
        <div class="punch-window-row">
          <div class="punch-window-row__label">上午工作时间</div>
          <el-time-picker v-model="halfDayDraft.am[0]" format="HH:mm" value-format="HH:mm" />
          <span>-</span>
          <el-time-picker v-model="halfDayDraft.am[1]" format="HH:mm" value-format="HH:mm" />
        </div>
        <div class="punch-window-row">
          <div class="punch-window-row__label">下午工作时间</div>
          <el-time-picker v-model="halfDayDraft.pm[0]" format="HH:mm" value-format="HH:mm" />
          <span>-</span>
          <el-time-picker v-model="halfDayDraft.pm[1]" format="HH:mm" value-format="HH:mm" />
        </div>
      </div>
      <template #footer>
        <el-button @click="halfDayDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveHalfDay">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="copySegmentDialogVisible" title="从已有规则复制上下班时间" width="920px" class="modern-dialog" append-to-body>
      <el-checkbox-group v-model="copySegmentSelectedKeys" class="copy-segment-list">
        <div v-for="item in copySegmentOptions" :key="item.key" class="copy-segment-item">
          <el-checkbox :label="item.key">
            <div class="copy-segment-item__rule">{{ item.ruleName }}</div>
            <div class="copy-segment-item__detail">{{ item.title }}</div>
          </el-checkbox>
        </div>
        <div v-if="!copySegmentOptions.length" class="segment-tip-muted">
          暂无可复制的上下班时间规则
        </div>
      </el-checkbox-group>
      <template #footer>
        <div class="segment-footer">
          <span class="segment-tip-muted">已选{{ (copySegmentSelectedKeys || []).length }}条上下班时间，最大可选7条</span>
          <div>
            <el-button type="primary" @click="confirmCopySegments">复制</el-button>
            <el-button @click="copySegmentDialogVisible = false">取消</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="holidayCalendarVisible" title="法定节假日日历" width="980px" class="modern-dialog holiday-calendar-dialog" append-to-body>
      <div class="holiday-toolbar">
        <el-select v-model="holidayCalendarYear" style="width: 110px">
          <el-option v-for="y in holidayYearOptions" :key="y" :label="`${y}年`" :value="y" />
        </el-select>
        <el-button @click="goToCurrentHolidayMonth">回到本月</el-button>
        <span class="holiday-source">按国务院办公厅当年节假日通知同步，补班按工作日打卡</span>
        <div class="holiday-legend">
          <span><i class="legend-dot legend-work"></i>工作日</span>
          <span><i class="legend-dot legend-rest"></i>休息日</span>
        </div>
      </div>
      <div class="holiday-month-switch">
        <el-button link @click="switchHolidayMonth(-1)">‹</el-button>
        <strong>{{ holidayCalendarYear }}年{{ holidayCalendarMonth }}月</strong>
        <el-button link @click="switchHolidayMonth(1)">›</el-button>
      </div>
      <div class="holiday-grid" v-loading="holidayCalendarLoading">
        <div v-for="head in ['周一', '周二', '周三', '周四', '周五', '周六', '周日']" :key="`h-${head}`" class="holiday-grid__head">{{ head }}</div>
        <div
          v-for="(cell, idx) in holidayCalendarCells"
          :key="cell.key"
          class="holiday-grid__cell"
          :class="{
            'is-out': !cell.inMonth,
            'is-workday': cell.dayType === 'work',
            'is-restday': cell.dayType === 'rest',
            'is-selected': cell.isToday,
            'is-last-col': (idx + 1) % 7 === 0,
          }"
        >
          <div class="holiday-grid__day">{{ cell.day }}</div>
          <div class="holiday-grid__tag" v-if="cell.tag">{{ cell.tag }}</div>
        </div>
      </div>
    </el-dialog>

    <el-dialog v-model="specialDateDialogVisible" :title="specialDateMode === 'must' ? '添加必须打卡的日期' : '添加无需打卡的日期'" width="700px" class="modern-dialog" append-to-body>
      <el-form :model="specialDateDraft" label-width="100px">
        <el-form-item label="添加方式">
          <el-radio-group v-model="specialDateDraft.mode">
            <el-radio value="single">单个日期</el-radio>
            <el-radio value="range">时间段</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="选择日期" v-if="specialDateDraft.mode === 'single'">
          <el-date-picker v-model="specialDateDraft.date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="日期范围" v-else>
          <el-date-picker v-model="specialDateDraft.date_range" type="daterange" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="重复">
          <el-select v-model="specialDateDraft.repeat" style="width: 100%">
            <el-option label="不重复" value="none" />
            <el-option label="每年重复" value="yearly" />
            <el-option label="每月重复" value="monthly" />
          </el-select>
        </el-form-item>
        <el-form-item label="打卡时间" v-if="!(ruleForm.extra.rule_type === 'free' && specialDateMode === 'no')">
          <div class="special-time-lines">
            <div v-for="(line, idx) in specialDateDraft.punch_times" :key="`line-${idx}`" class="special-time-line">
              <el-time-picker v-model="line.start" format="HH:mm" value-format="HH:mm" placeholder="上班" />
              <span>—</span>
              <el-time-picker v-model="line.end" format="HH:mm" value-format="HH:mm" placeholder="下班" />
              <el-button v-if="specialDateDraft.punch_times.length > 1" type="danger" link @click="removeSpecialDateTime(idx)">删除</el-button>
            </div>
          </div>
          <el-button link type="primary" @click="addSpecialDateTime"><el-icon><Plus /></el-icon> 添加时段</el-button>
        </el-form-item>
        <el-form-item label="特殊事由">
          <el-input v-model="specialDateDraft.reason" placeholder="如：季度盘点/客户验厂/年会值班" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="specialDateDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveSpecialDate">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="restdaySettingDialogVisible"
      :title="ruleForm.extra.rule_type === 'free' ? '打卡设置' : '休息日'"
      width="980px"
      class="modern-dialog restday-setting-dialog"
      append-to-body
    >
      <div class="restday-setting-subtitle" v-if="ruleForm.extra.rule_type !== 'free'">休息日和节假日允许加班时，成员打卡将遵循以下设置</div>
      <el-form label-width="130px" class="restday-setting-form">
        <el-form-item label="打卡交替方式">
          <el-radio-group v-model="restdaySettingDraft.shift_mode" class="restday-shift-mode">
            <div class="restday-shift-mode__row">
              <div
                class="restday-shift-mode__option"
                :class="{ 'is-active': restdaySettingDraft.shift_mode === 'single' }"
                @click="restdaySettingDraft.shift_mode = 'single'"
              >
                <el-radio value="single" />
                <span class="restday-shift-mode__choice">交替打卡1次</span>
              </div>
              <span class="restday-shift-mode__desc">打卡上班-下班-更新下班，以最早最晚打卡计算工作时长</span>
            </div>
            <div class="restday-shift-mode__row">
              <div
                class="restday-shift-mode__option"
                :class="{ 'is-active': restdaySettingDraft.shift_mode === 'multi' }"
                @click="restdaySettingDraft.shift_mode = 'multi'"
              >
                <el-radio value="multi" />
                <span class="restday-shift-mode__choice">交替打卡多次</span>
              </div>
              <span class="restday-shift-mode__desc">打卡上班-下班-上班，依次交替，以多次交替上下班打卡计算时长</span>
            </div>
            <div class="restday-shift-mode__row" v-if="ruleForm.extra.rule_type === 'free'">
              <div
                class="restday-shift-mode__option"
                :class="{ 'is-active': restdaySettingDraft.shift_mode === 'record_only' }"
                @click="restdaySettingDraft.shift_mode = 'record_only'"
              >
                <el-radio value="record_only" />
                <span class="restday-shift-mode__choice">仅记录打卡时间和位置</span>
              </div>
              <span class="restday-shift-mode__desc">无需上下班交替打卡，不计算工作/加班时长</span>
            </div>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="打卡间隔时间" v-if="restdaySettingDraft.shift_mode !== 'record_only'">
          <div class="restday-interval">
            <span>每次上班打卡</span>
            <el-select v-model="restdaySettingDraft.interval_minutes" style="width: 180px">
              <el-option
                v-for="v in restdayIntervalOptions"
                :key="`restday-${v}`"
                :label="`${v}分钟`"
                :value="v"
              />
            </el-select>
            <span>后可打下班卡</span>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="saveRestdaySetting">保存</el-button>
        <el-button @click="restdaySettingDialogVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="leavePunchSettingDialogVisible"
      title="请假时打卡设置"
      width="980px"
      class="modern-dialog leave-punch-setting-dialog"
      append-to-body
    >
      <div class="leave-punch-setting-subtitle">
        可设置成员按半天/按小时请假时，需在离岗前和返岗后打卡。
        <el-button link type="primary" class="leave-punch-setting-link" @click="openLeavePunchLearnMore">了解更多</el-button>
      </div>
      <div class="leave-punch-setting-check">
        <el-checkbox v-model="leavePunchSettingDraft.need_punch">请假时离岗/返岗需打卡</el-checkbox>
      </div>
      <div class="leave-punch-setting-hint">
        半天请假的打卡时间可在「上下班时间-半天工作时间」设置。
        <el-button link type="primary" class="leave-punch-setting-link" @click="handleLeavePunchNavigateSetting">前往设置</el-button>
      </div>
      <div class="leave-punch-setting-panel" :class="{ 'is-disabled': !leavePunchSettingDraft.need_punch }">
        <span>可打卡时段：</span>
        <span>离岗打卡最多可提前</span>
        <el-select
          v-model="leavePunchSettingDraft.before_max_advance"
          class="leave-punch-setting-select"
          :disabled="!leavePunchSettingDraft.need_punch"
        >
          <el-option
            v-for="item in leavePunchTimeLimitOptions"
            :key="`leave-before-limit-${item}`"
            :label="item"
            :value="item"
          />
        </el-select>
        <span>，返岗打卡最多可延迟</span>
        <el-select
          v-model="leavePunchSettingDraft.after_max_delay"
          class="leave-punch-setting-select"
          :disabled="!leavePunchSettingDraft.need_punch"
        >
          <el-option
            v-for="item in leavePunchTimeLimitOptions"
            :key="`leave-after-limit-${item}`"
            :label="item"
            :value="item"
          />
        </el-select>
      </div>
      <template #footer>
        <el-button type="primary" @click="saveLeavePunchSetting">确定</el-button>
        <el-button @click="leavePunchSettingDialogVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="overtimeRuleDialogVisible"
      title="加班规则"
      width="980px"
      class="modern-dialog overtime-rule-dialog"
      append-to-body
    >
      <div class="overtime-rule-tabs">
        <button
          v-for="tab in overtimeDayTabs"
          :key="`ot-tab-${tab.value}`"
          type="button"
          class="overtime-rule-tab"
          :class="{ 'is-active': overtimeRuleActiveTab === tab.value }"
          @click="overtimeRuleActiveTab = tab.value"
        >{{ tab.label }}</button>
      </div>

      <div class="overtime-rule-body">
        <div class="overtime-rule-line overtime-rule-line--allow">
          <el-checkbox v-model="activeOvertimeDayRule.enabled" label="允许加班" />
          <span v-if="overtimeRuleActiveTab === 'holiday'" class="overtime-rule-holiday-tip">
            法定节假日加班记为「节假日加班」
          </span>
        </div>

        <template v-if="activeOvertimeDayRule.enabled">
          <div class="overtime-rule-line" v-if="overtimeRuleActiveTab === 'workday'">
            <div class="overtime-rule-line__label">加班时段</div>
            <div class="overtime-rule-line__value">
              <el-radio-group v-model="activeOvertimeDayRule.period_mode">
                <el-radio value="all">允许全天</el-radio>
                <el-radio value="before_work">仅允许上班前</el-radio>
                <el-radio value="after_work">仅允许下班后</el-radio>
              </el-radio-group>
            </div>
          </div>

          <div class="overtime-rule-line">
            <div class="overtime-rule-line__label">计算方式</div>
            <div class="overtime-rule-line__value overtime-method-cards">
              <div
                v-for="card in overtimeCalcMethodOptions"
                :key="`ot-method-${card.value}`"
                class="overtime-method-card"
                :class="{ 'is-active': activeOvertimeDayRule.calc_method === card.value }"
                @click="activeOvertimeDayRule.calc_method = card.value"
              >
                <strong>{{ card.label }}</strong>
                <span>{{ card.desc }}</span>
              </div>
            </div>
          </div>

          <div class="overtime-rule-line">
            <div class="overtime-rule-line__label">计算规则</div>
            <div class="overtime-rule-line__value">
              <div class="overtime-rule-panel">
                <div class="overtime-inline-row" v-if="overtimeRuleActiveTab === 'workday' && activeOvertimeDayRule.calc_method === 'by_clock' && activeOvertimeDayRule.period_mode !== 'before_work'">
                  <span>下班后</span>
                  <el-input-number v-model="activeOvertimeDayRule.start_after_off_duty_minutes" :min="0" :max="720" :step="5" controls-position="right" style="width: 110px" />
                  <span>分钟开始计算加班</span>
                </div>
                <div class="overtime-inline-row" v-if="activeOvertimeDayRule.calc_method === 'by_clock'">
                  <span>最短加班时长</span>
                  <el-input-number v-model="activeOvertimeDayRule.min_minutes" :min="0" :max="1440" :step="5" controls-position="right" style="width: 110px" />
                  <span>分钟</span>
                  <span class="segment-tip-muted">不足则记录为未加班</span>
                </div>
                <div class="overtime-inline-row" v-if="activeOvertimeDayRule.calc_method === 'by_clock'">
                  <span>最长加班时长</span>
                  <el-input-number v-model="activeOvertimeDayRule.max_minutes" :min="0" :max="1440" :step="5" controls-position="right" style="width: 110px" />
                  <span>分钟</span>
                  <span class="segment-tip-muted">超过则按该时长封顶</span>
                </div>

                <div class="overtime-divider"></div>

                <div class="overtime-inline-row">
                  <el-checkbox v-model="activeOvertimeDayRule.allow_rest_deduction" label="允许扣除休息时间" />
                </div>

                <template v-if="activeOvertimeDayRule.allow_rest_deduction">
                  <div class="overtime-inline-row">
                    <el-radio-group v-model="activeOvertimeDayRule.rest_deduction_mode">
                      <el-radio value="period">按休息时段扣除</el-radio>
                      <el-radio value="duration">按加班时长扣除</el-radio>
                    </el-radio-group>
                  </div>

                  <div class="overtime-rest-period-list" v-if="activeOvertimeDayRule.rest_deduction_mode === 'period'">
                    <div
                      v-for="(item, idx) in activeOvertimeDayRule.rest_periods"
                      :key="`overtime-rest-${overtimeRuleActiveTab}-${idx}`"
                      class="rest-time-row"
                    >
                      <div class="rest-time-row__label">休息时段{{ idx + 1 }}</div>
                      <el-time-picker
                        v-model="item.start"
                        format="HH:mm"
                        value-format="HH:mm"
                        placeholder="开始时间"
                      />
                      <span>-</span>
                      <el-time-picker
                        v-model="item.end"
                        format="HH:mm"
                        value-format="HH:mm"
                        placeholder="结束时间"
                      />
                      <div class="rest-time-row__ops">
                        <el-button circle @click="removeOvertimeRestPeriod(idx)" :disabled="activeOvertimeDayRule.rest_periods.length === 1">
                          <el-icon><Minus /></el-icon>
                        </el-button>
                        <el-button circle @click="addOvertimeRestPeriod">
                          <el-icon><Plus /></el-icon>
                        </el-button>
                      </div>
                    </div>
                  </div>

                  <div class="overtime-inline-row" v-else>
                    <span>加班每满</span>
                    <el-input-number v-model="activeOvertimeDayRule.deduct_every_minutes" :min="0" :max="720" :step="5" controls-position="right" style="width: 110px" />
                    <span>分钟，扣除</span>
                    <el-input-number v-model="activeOvertimeDayRule.deduct_minutes" :min="0" :max="720" :step="5" controls-position="right" style="width: 110px" />
                    <span>分钟</span>
                  </div>
                </template>
              </div>
            </div>
          </div>

          <div class="overtime-rule-line">
            <div class="overtime-rule-line__label">核算方式</div>
            <div class="overtime-rule-line__value">
              <div class="overtime-rule-panel">
                <div class="overtime-inline-row">
                  <el-checkbox v-model="activeOvertimeDayRule.allow_convert" label="允许加班时长计为调休或加班费" />
                </div>
                <template v-if="activeOvertimeDayRule.allow_convert">
                  <div class="overtime-inline-row">
                    <el-radio-group v-model="activeOvertimeDayRule.convert_mode">
                      <el-radio value="comp_time">计为调休</el-radio>
                      <el-radio value="overtime_pay">计为加班费</el-radio>
                    </el-radio-group>
                  </div>
                  <div class="overtime-inline-row" v-if="activeOvertimeDayRule.convert_mode === 'comp_time'">
                    <span>调休时长按照 1 :</span>
                    <el-input-number v-model="activeOvertimeDayRule.comp_time_ratio" :min="0.1" :max="5" :step="0.1" :precision="2" controls-position="right" style="width: 110px" />
                  </div>
                  <div class="overtime-inline-row" v-if="activeOvertimeDayRule.convert_mode === 'comp_time'">
                    <el-checkbox v-model="activeOvertimeDayRule.sync_auto_leave_type" label="同步「加班自动调休」的假期：调休" />
                  </div>
                </template>
              </div>
            </div>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button type="primary" @click="saveOvertimeRuleDialog">保存</el-button>
        <el-button @click="overtimeRuleDialogVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="overtimeDurationDialogVisible"
      title="加班时长设置"
      width="680px"
      class="modern-dialog"
      append-to-body
    >
      <el-form label-width="110px">
        <el-form-item label="加班单位">
          <el-radio-group v-model="overtimeDurationDraft.unit">
            <el-radio value="hour">小时</el-radio>
            <el-radio value="day">天</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="取整方式">
          <el-select v-model="overtimeDurationDraft.rounding_mode" style="width: 240px">
            <el-option label="四舍五入" value="round" />
            <el-option label="向上取整" value="ceil" />
            <el-option label="向下取整" value="floor" />
          </el-select>
          <span class="field-unit">保留</span>
          <el-input-number v-model="overtimeDurationDraft.decimal_places" :min="0" :max="3" controls-position="right" style="width: 110px; margin-left: 6px;" />
          <span class="field-unit">位小数</span>
        </el-form-item>
        <el-form-item label="单位换算">
          <span>1天 =</span>
          <el-input-number v-model="overtimeDurationDraft.day_to_hours" :min="1" :max="24" :step="0.5" :precision="1" controls-position="right" style="width: 120px; margin: 0 8px;" />
          <span>小时</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="saveOvertimeDurationDialog">保存</el-button>
        <el-button @click="overtimeDurationDialogVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="shiftArrangeDialogVisible"
      title="排班"
      width="96%"
      top="2vh"
      class="modern-dialog shift-arrange-dialog"
      append-to-body
    >
      <div class="shift-arrange-panel">
        <div class="shift-arrange-row">
          <div class="shift-arrange-label"><span class="required-star">*</span>排班方式</div>
          <div class="shift-arrange-value">
            <div class="shift-arrange-mode-cards">
              <div class="shift-arrange-mode-card" :class="{ 'is-active': ruleForm.extra.shift_arrange_mode === 'manual' }" @click="ruleForm.extra.shift_arrange_mode = 'manual'">
                <strong>手动排班</strong>
                <span>成员按排班表打卡</span>
              </div>
              <div class="shift-arrange-mode-card" :class="{ 'is-active': ruleForm.extra.shift_arrange_mode === 'optional' }" @click="ruleForm.extra.shift_arrange_mode = 'optional'">
                <strong>无需事先排班</strong>
                <span>成员自选班次或系统自动对班</span>
              </div>
            </div>
          </div>
        </div>

        <div class="shift-arrange-row">
          <div class="shift-arrange-label"><span class="required-star">*</span>打卡班次</div>
          <div class="shift-arrange-value">
            <div class="shift-class-tags">
              <el-tag
                v-for="item in ruleForm.extra.shift_templates || []"
                :key="`shift-tag-${item.id || item.name}`"
                :style="{ backgroundColor: item.color || '#E2E8F0', borderColor: 'transparent', color: '#334155' }"
              >{{ item.name }} {{ item.clock_in }}-{{ hmToMinutes(item.clock_out) < hmToMinutes(item.clock_in) ? `次日${item.clock_out}` : item.clock_out }}</el-tag>
              <el-button link type="primary" @click="openShiftClassDrawer">修改</el-button>
            </div>
          </div>
        </div>

        <div class="shift-arrange-row" v-if="ruleForm.extra.shift_arrange_mode === 'manual'">
          <div class="shift-arrange-label">排班周期</div>
          <div class="shift-arrange-value"><el-button link type="primary">添加</el-button></div>
        </div>
        <div class="shift-arrange-row" v-if="ruleForm.extra.shift_arrange_mode === 'manual'">
          <div class="shift-arrange-label">协助管理</div>
          <div class="shift-arrange-value"><el-button link type="primary">添加</el-button><span class="hint-text">邀请店长/组长等一起排班</span></div>
        </div>

        <div class="shift-arrange-row">
          <div class="shift-arrange-label">更多设置</div>
          <div class="shift-arrange-value shift-arrange-more">
            <el-checkbox v-model="ruleForm.extra.shift_no_schedule_employee_select">未排班时，成员自选班次</el-checkbox>
            <el-checkbox v-model="ruleForm.extra.shift_no_schedule_system_match">未排班时，系统自动对班</el-checkbox>
            <div class="shift-match-window" v-if="ruleForm.extra.shift_no_schedule_system_match">
              <span>对班时段：班次上班时间前</span>
              <el-select v-model="ruleForm.extra.shift_match_before_minutes" style="width: 130px">
                <el-option v-for="m in shiftMatchMinuteOptions" :key="`before-${m}`" :value="m" :label="`${m}分钟`" />
              </el-select>
              <span>至到次上班时间后</span>
              <el-select v-model="ruleForm.extra.shift_match_after_minutes" style="width: 130px">
                <el-option v-for="m in shiftMatchMinuteOptions" :key="`after-${m}`" :value="m" :label="`${m}分钟`" />
              </el-select>
            </div>
            <el-checkbox v-model="ruleForm.extra.shift_allow_change_apply">成员可发起调班申请，审批通过后更改班次</el-checkbox>
          </div>
        </div>
      </div>

      <div class="shift-roster-table" v-loading="shiftArrangeLoading">
        <div class="shift-roster-toolbar">
          <el-date-picker v-model="shiftArrangeMonth" type="month" value-format="YYYY-MM" style="width: 140px" />
          <el-input v-model="shiftArrangeKeyword" placeholder="搜索成员" style="width: 220px" />
        </div>
        <table>
          <thead>
            <tr>
              <th>姓名</th>
              <th v-for="d in shiftArrangeDays" :key="`th-${d.key}`">
                <div>{{ d.day }}</div>
                <div>周{{ d.weekText }}</div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!shiftArrangeEmployees.length">
              <td :colspan="shiftArrangeDays.length + 1" class="empty-cell">请添加打卡人员</td>
            </tr>
            <tr v-for="emp in shiftArrangeEmployees" :key="`row-${emp.id}`">
              <td class="name-col">{{ emp.name }}</td>
              <td v-for="d in shiftArrangeDays" :key="`cell-${emp.id}-${d.key}`">
                <el-select
                  v-model="shiftAssignMap[shiftCellKey(emp.id, d.key)]"
                  placeholder="-"
                  size="small"
                  style="width: 92px"
                  @change="saveShiftAssignment(emp.id, d.key)"
                >
                  <el-option
                    v-for="tpl in (ruleForm.extra.shift_templates || [])"
                    :key="`opt-${templateValueKey(tpl)}`"
                    :label="tpl.name"
                    :value="templateValueKey(tpl)"
                  />
                </el-select>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template #footer>
        <el-button type="primary" @click="shiftArrangeDialogVisible = false">确认</el-button>
        <el-button @click="shiftArrangeDialogVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="shiftClassDrawerVisible" title="配置班次" size="40%" class="shift-class-drawer" append-to-body>
      <div class="shift-class-drawer__toolbar">
        <el-button @click="openShiftClassEditor(-1)">添加</el-button>
      </div>
      <el-table :data="ruleForm.extra.shift_templates || []" class="modern-table" max-height="560">
        <el-table-column label="班次名称">
          <template #default="{ row }">
            <span class="shift-dot" :style="{ backgroundColor: row.color || '#CBD5E1' }"></span>
            {{ row.name }}
          </template>
        </el-table-column>
        <el-table-column label="上下班时间">
          <template #default="{ row }">{{ row.clock_in }}-{{ hmToMinutes(row.clock_out) < hmToMinutes(row.clock_in) ? `次日${row.clock_out}` : row.clock_out }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ $index }">
            <el-button type="primary" link @click="openShiftClassEditor($index)">编辑</el-button>
            <el-button type="danger" link @click="removeShiftClass($index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button type="primary" @click="shiftClassDrawerVisible = false">确认</el-button>
        <el-button @click="shiftClassDrawerVisible = false">取消</el-button>
      </template>
    </el-drawer>

    <el-dialog v-model="shiftClassEditorVisible" :title="shiftClassEditingIndex >= 0 ? '编辑班次' : '添加班次'" width="980px" class="modern-dialog shift-class-editor" append-to-body>
      <el-form label-width="110px">
        <el-form-item label="班次名称" required>
          <el-input v-model="shiftClassDraft.name" placeholder="例：早班。不能超过8个字符" />
        </el-form-item>
        <el-form-item label="工作时间" required>
          <div class="segment-panel" style="width:100%;">
            <div class="segment-time-line">
              <div class="segment-time-title">上班时间</div>
              <el-time-picker v-model="shiftClassDraft.clock_in" format="HH:mm" value-format="HH:mm" style="width: 280px" />
              <el-select v-model="shiftClassDraft.check_in_required" style="width: 140px">
                <el-option label="需打卡" :value="true" />
                <el-option label="无需打卡" :value="false" />
              </el-select>
            </div>
            <div class="segment-time-line">
              <div class="segment-time-title">下班时间</div>
              <el-time-picker v-model="shiftClassDraft.clock_out" format="HH:mm" value-format="HH:mm" style="width: 280px" />
              <el-select v-model="shiftClassDraft.check_out_required" style="width: 140px">
                <el-option label="需打卡" :value="true" />
                <el-option label="无需打卡" :value="false" />
              </el-select>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <div class="segment-row">
        <div class="segment-label">可打卡时段</div>
        <div class="segment-value">
          <span>{{ formatPunchWindow(shiftClassDraft.punch_start, shiftClassDraft.punch_end) }}</span>
          <el-button link type="primary" @click="shiftClassDraft.punch_start = minutesToHm(hmToMinutes(shiftClassDraft.clock_in) - 300); shiftClassDraft.punch_end = minutesToHm(hmToMinutes(shiftClassDraft.clock_out) + 240)">修改</el-button>
        </div>
      </div>
      <div class="segment-row">
        <div class="segment-label">弹性打卡</div>
        <div class="segment-value">
          <el-radio-group v-model="shiftClassDraft.flex_mode">
            <el-radio value="none">不允许</el-radio>
            <el-radio value="late_leave">允许晚到早走</el-radio>
            <el-radio value="both">允许早到早走、晚到晚走</el-radio>
          </el-radio-group>
        </div>
      </div>
      <div class="segment-row">
        <div class="segment-label">晚走晚到</div>
        <div class="segment-value">
          <el-checkbox v-model="shiftClassDraft.late_leave_next_day">下班晚走，次日可晚到</el-checkbox>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" :loading="shiftClassSubmitting" @click="saveShiftClass">保存</el-button>
        <el-button @click="shiftClassEditorVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="shiftTemplateDialogVisible" title="配置班次模板" width="760px" class="modern-dialog" append-to-body>
      <div class="shift-template-builder">
        <el-input v-model="shiftTemplateDraft.name" placeholder="班次名称，如：A班" />
        <el-time-picker v-model="shiftTemplateDraft.clock_in" format="HH:mm" value-format="HH:mm" placeholder="上班时间" />
        <el-time-picker v-model="shiftTemplateDraft.clock_out" format="HH:mm" value-format="HH:mm" placeholder="下班时间" />
        <el-button @click="addShiftTemplate">添加</el-button>
      </div>
      <el-table :data="ruleForm.extra.shift_templates || []" class="modern-table" max-height="300">
        <el-table-column prop="name" label="班次名称" />
        <el-table-column label="工作时间">
          <template #default="{ row }">{{ row.clock_in }} - {{ row.clock_out }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center">
          <template #default="{ $index }">
            <el-button type="danger" link @click="removeShiftTemplate($index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="shiftTemplateDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="locationDialogVisible" title="添加打卡地点" width="620px" class="modern-dialog wework-location-dialog" append-to-body>
      <div class="wework-location-body">
        <div class="wework-location-row">
          <el-select v-model="locationDraft.country" placeholder="国家/地区" class="wework-location-country">
            <el-option label="中国大陆" value="中国大陆" />
            <el-option label="中国香港" value="中国香港" />
            <el-option label="中国澳门" value="中国澳门" />
            <el-option label="中国台湾" value="中国台湾" />
          </el-select>
          <el-autocomplete
            v-model="locationDraft.name"
            :fetch-suggestions="queryLocationSuggestions"
            placeholder="请输入公司打卡地址，例如腾讯大厦"
            clearable
            value-key="value"
            class="wework-location-search"
            popper-class="wework-location-suggest-popper"
            @select="handleLocationSuggestionSelect"
          >
            <template #default="{ item }">
              <div class="wework-location-suggest-row">
                <span class="wework-location-suggest-row__name">{{ item.title || item.value }}</span>
                <span class="wework-location-suggest-row__addr">{{ item.address || '' }}</span>
              </div>
            </template>
          </el-autocomplete>
        </div>

        <div class="import-entry-row wework-location-links">
          <el-button link type="primary" @click="mockImportEntry('location_phone')">手机上报</el-button>
          <span>|</span>
          <el-button link type="primary" @click="mockImportEntry('location_batch')">批量导入</el-button>
          <span>|</span>
          <el-button link type="primary" @click="mockImportEntry('location_tpl')">下载模板</el-button>
        </div>
        <div class="wework-location-build-tag">{{ LOCATION_DIALOG_BUILD_TAG }}</div>
      </div>
    </el-dialog>

    <el-dialog v-model="wifiDialogVisible" title="打卡WiFi" width="820px" class="modern-dialog wifi-dialog" append-to-body>
      <div class="wifi-form">
        <div class="wifi-field-row">
          <div class="wifi-field-label"><span class="required-star">*</span>WiFi名称</div>
          <el-input v-model="wifiDraft.name" class="wifi-ssid-input" placeholder="请输入WiFi名称" />
        </div>
        <div class="wifi-field-row wifi-field-row--bssid">
          <div class="wifi-field-label"><span class="required-star">*</span>BSSID</div>
          <div class="wifi-bssid-group">
            <template v-for="(_, idx) in wifiBssidParts" :key="`bssid-${idx}`">
              <el-input
                v-model="wifiBssidParts[idx]"
                class="wifi-bssid-input"
                maxlength="2"
                @input="onWifiBssidInput(idx, $event)"
                @paste="onWifiBssidPaste"
              />
              <span v-if="idx < wifiBssidParts.length - 1" class="wifi-bssid-colon">:</span>
            </template>
          </div>
        </div>
      </div>

      <div class="wifi-help">
        <div class="wifi-help__title">BSSID获取方式</div>
        <div class="wifi-help__panel">
          <div class="wifi-help__text">
            <div>1. 可以向公司IT部门询问公司WiFi的BSSID</div>
            <div>2. 也可以通过使用最新版企业微信扫描右侧二维码获取</div>
            <div class="wifi-help__tip">* 请确保路由器BSSID不会动态变化，否则在连接Wi-Fi后可能出现不在打卡范围内等异常</div>
          </div>
          <div class="wifi-help__split" />
          <div class="wifi-help__qrcode-wrap">
            <div class="wifi-help__qrcode" />
          </div>
        </div>
      </div>
      <template #footer>
        <div class="wifi-dialog-footer">
          <div class="wifi-dialog-footer__links">
            <el-button link type="primary" @click="mockImportEntry('wifi_batch')">批量导入</el-button>
            <span>|</span>
            <el-button link type="primary" @click="mockImportEntry('wifi_tpl')">下载模板</el-button>
          </div>
          <div class="wifi-dialog-footer__actions">
            <el-button type="primary" @click="addWifi">确认</el-button>
            <el-button @click="wifiDialogVisible = false">取消</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="ruleLogsVisible" :title="`规则变更历史 - ${currentRuleName || ''}`" width="720px" destroy-on-close class="modern-dialog">
      <el-table :data="ruleLogs" class="modern-table" max-height="420">
        <el-table-column prop="created_at" label="时间" width="190" />
        <el-table-column prop="operator_name" label="操作人" width="120" />
        <el-table-column prop="action" label="动作" width="100" />
        <el-table-column prop="content" label="内容" min-width="240" />
      </el-table>
    </el-dialog>

    <el-drawer v-model="recordDetailVisible" title="打卡详情" size="420px">
      <template v-if="recordDetail">
        <div class="detail-row"><span>员工</span><strong>{{ recordDetail.employee_name }} {{ recordDetail.employee_no ? `(${recordDetail.employee_no})` : '' }}</strong></div>
        <div class="detail-row"><span>部门</span><strong>{{ recordDetail.department_name || '-' }}</strong></div>
        <div class="detail-row"><span>日期</span><strong>{{ recordDetail.date }}</strong></div>
        <div class="detail-row"><span>上班打卡</span><strong>{{ recordDetail.clock_in || '--:--' }}</strong></div>
        <div class="detail-row"><span>下班打卡</span><strong>{{ recordDetail.clock_out || '--:--' }}</strong></div>
        <div class="detail-row"><span>应出勤工时</span><strong>{{ recordDetail.expected_hours }}h</strong></div>
        <div class="detail-row"><span>实际工时</span><strong>{{ recordDetail.actual_hours }}h</strong></div>
        <div class="detail-row"><span>状态</span><strong>{{ recordDetail.display_status }}</strong></div>
        <div class="detail-row"><span>设备信息</span><strong>{{ recordDetail.device_info || '-' }}</strong></div>
        <div class="detail-row"><span>打卡位置</span><strong>{{ recordDetail.clock_in_location || '-' }}</strong></div>
        <div class="detail-row"><span>历史异常</span><strong>{{ recordDetail.anomaly_type || '-' }}</strong></div>
        <div v-if="isExceptionStatus(recordDetail.display_status)" class="record-detail-actions">
          <div class="record-detail-actions__hint">
            <strong>处理异常</strong>
            <span>先确认打卡详情，再选择补卡、假勤或校准为正常。</span>
          </div>
          <div class="record-detail-actions__buttons">
            <el-button type="primary" @click="openRecordInAnomalyWorkbench(recordDetail)">查看审批/异常</el-button>
            <el-button type="success" plain @click="markRecordAsNormal(recordDetail)">标记正常</el-button>
          </div>
        </div>
      </template>
    </el-drawer>

    <el-drawer v-model="appealDetailVisible" title="审批/异常详情" size="560px" destroy-on-close>
      <template v-if="appealDetailLoading">
        <el-skeleton :rows="8" animated />
      </template>
      <template v-else-if="appealDetail">
        <div class="appeal-detail">
          <div class="appeal-detail__header">
            <div>
              <div class="appeal-detail__title">{{ appealDetail.type_label || appealDetail.approval_type_name || '审批申请' }}</div>
              <div class="appeal-detail__sub">{{ appealDetail.employee_name || appealDetail.applicant_name || '-' }} · {{ appealDetail.created_at || '-' }}</div>
            </div>
            <el-tag :type="approvalStatusTagType(appealDetail.status)">
              {{ approvalStatusText(appealDetail.status) }}
            </el-tag>
          </div>

          <el-descriptions :column="1" border>
            <el-descriptions-item label="来源">{{ appealSourceText(appealDetail.source) }}</el-descriptions-item>
            <el-descriptions-item label="申请类型">{{ appealDetail.type_label || '-' }}</el-descriptions-item>
            <el-descriptions-item label="关联日期">{{ appealDetail.related_date || '-' }}</el-descriptions-item>
            <el-descriptions-item label="打卡/申请时间">{{ appealDetail.punch_time || '-' }}</el-descriptions-item>
            <el-descriptions-item label="摘要/原因">{{ appealDetail.summary || '-' }}</el-descriptions-item>
            <el-descriptions-item label="当前节点/处理人">{{ appealDetail.current_handler || '-' }}</el-descriptions-item>
          </el-descriptions>

          <div v-if="approvalFormRows.length" class="appeal-detail__section">
            <h3>申请表单</h3>
            <div class="appeal-form-grid">
              <div v-for="item in approvalFormRows" :key="item.key" class="appeal-form-row">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </div>
            </div>
          </div>

          <div v-if="approvalRecordRows.length" class="appeal-detail__section">
            <h3>审批记录</h3>
            <el-timeline>
              <el-timeline-item
                v-for="item in approvalRecordRows"
                :key="item.id || `${item.node_order}-${item.acted_at}`"
                :timestamp="formatDateTime(item.acted_at || item.created_at)"
              >
                <div class="timeline-action">{{ approvalActionText(item.action) }} · {{ item.approver_name || `审批人#${item.approver_id || '-'}` }}</div>
                <div v-if="item.comment" class="timeline-comment">{{ item.comment }}</div>
              </el-timeline-item>
            </el-timeline>
          </div>
        </div>
      </template>
      <el-empty v-else description="暂无详情" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import request, { get, post, put, del } from '@/utils/request'
import { useDepartmentTree } from '@/composables/useDepartmentTree'
import { useResponsiveLayout } from '@/composables/useResponsiveLayout'
import {
  UserFilled, Clock, Calendar, Position, Location,
  Search, Plus, Download, Refresh, Minus, Close, QuestionFilled
} from '@element-plus/icons-vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent])

// ============ State ============
const route = useRoute()
const activeTab = ref(route.query.tab === 'rules' ? 'rules' : 'records')
watch(() => route.query.tab, tab => {
  if (tab === 'rules') { activeTab.value = 'rules'; fetchRules() }
})
const reportTab = ref<'daily-report' | 'summary'>('daily-report')
const {
  departments: deptOptions,
  departmentTree: deptTreeOptions,
  rootExpandedKeys: deptDefaultExpandedKeys,
  ensureDepartmentsLoaded,
  onDepartmentSelectVisibleChange,
} = useDepartmentTree()
const employeeOptions = ref<any[]>([])
const employeeMap = ref<Record<number, any>>({})
const assigneeCompanies = ref<any[]>([])
const assigneeCompanyLayouts = ref<any[]>([])
const { isMobile, isLandscapeCompact } = useResponsiveLayout()
const autoRefreshTimer = ref<number | null>(null)
const quickFilter = ref<'all' | 'today_anomaly' | 'week_late' | 'pending_appeal'>('all')

function handleDeptTreeVisibleChange(visible: boolean) {
  onDepartmentSelectVisibleChange(visible)
}

const todayStats = reactive({
  present: 0,
  total: 0,
  late: 0,
  leave: 0,
  outside: 0
})

const trendDays = ref<string[]>([])
const presentTrend = ref<number[]>([])
const lateTrend = ref<number[]>([])
const leaveTrend = ref<number[]>([])
const outsideTrend = ref<number[]>([])

const presentTrendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 20, right: 14, top: 10, bottom: 20, containLabel: true },
  xAxis: { type: 'category', data: trendDays.value, axisLabel: { color: '#64748B' } },
  yAxis: { type: 'value', axisLabel: { color: '#94A3B8' }, splitLine: { lineStyle: { color: '#EEF2FF' } } },
  series: [{ type: 'bar', data: presentTrend.value, barMaxWidth: 20, itemStyle: { color: '#4F46E5', borderRadius: [4, 4, 0, 0] } }],
}))

const lateTrendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 20, right: 14, top: 10, bottom: 20, containLabel: true },
  xAxis: { type: 'category', data: trendDays.value, axisLabel: { color: '#64748B' } },
  yAxis: { type: 'value', axisLabel: { color: '#94A3B8' }, splitLine: { lineStyle: { color: '#EEF2FF' } } },
  series: [{ type: 'line', smooth: true, data: lateTrend.value, itemStyle: { color: '#FA8C16' }, areaStyle: { color: 'rgba(250,140,22,0.15)' } }],
}))

const leaveDistOption = computed(() => ({
  tooltip: { trigger: 'item' },
  legend: { bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: '#64748B', fontSize: 12 } },
  series: [{
    type: 'pie',
    radius: ['45%', '70%'],
    center: ['50%', '45%'],
    label: { show: true, formatter: '{d}%' },
    data: [
      { name: '请假', value: leaveTrend.value.reduce((s, x) => s + x, 0), itemStyle: { color: '#1890FF' } },
      { name: '出差', value: outsideTrend.value.reduce((s, x) => s + x, 0), itemStyle: { color: '#722ED1' } },
    ],
  }],
}))

const flatDeptOptions = computed(() => {
  const out: any[] = []
  const walk = (arr: any[]) => {
    for (const n of (arr || [])) {
      out.push({ id: n.id, name: n.name })
      if (n.children?.length) walk(n.children)
    }
  }
  walk(deptOptions.value || [])
  return out
})

const employeeLabelOptions = computed(() => {
  const pool = new Set<string>()
  const tryPush = (v: any) => {
    if (!v) return
    const text = String(v).trim()
    if (text) pool.add(text)
  }
  const tryArray = (arr: any) => {
    if (!Array.isArray(arr)) return
    arr.forEach((item: any) => {
      if (typeof item === 'string') tryPush(item)
      else if (item && typeof item === 'object') tryPush(item.name || item.label || item.tag)
    })
  }
  for (const e of employeeOptions.value || []) {
    tryArray(e.tags)
    tryArray(e.talent_tags)
    if (typeof e.tags === 'string') {
      e.tags.split(/[，,;；]/).forEach((x: string) => tryPush(x))
    }
    if (typeof e.talent_tags === 'string') {
      try {
        const parsed = JSON.parse(e.talent_tags)
        tryArray(parsed)
      } catch {
        e.talent_tags.split(/[，,;；]/).forEach((x: string) => tryPush(x))
      }
    }
    if (typeof e.skill_tags === 'string') {
      try {
        const parsed = JSON.parse(e.skill_tags)
        tryArray(parsed)
      } catch {}
    }
  }
  return Array.from(pool).sort((a, b) => a.localeCompare(b, 'zh-CN'))
})

const attendanceGroupOptions = computed(() => {
  const pool = new Set<string>()
  const pushText = (v: any) => {
    if (!v) return
    const text = String(v).trim()
    if (text) pool.add(text)
  }
  for (const r of rules.value || []) {
    const extra = r?.extra || r?.extra_config || {}
    if (Array.isArray(extra.attendance_groups)) extra.attendance_groups.forEach(pushText)
    pushText(extra.attendance_group)
  }
  if (Array.isArray(ruleForm.value?.extra?.attendance_groups)) {
    ruleForm.value.extra.attendance_groups.forEach(pushText)
  }
  return Array.from(pool).sort((a, b) => a.localeCompare(b, 'zh-CN'))
})

// ============ Tag type helpers ============
function attendanceStatusType(v: string) {
  const m: Record<string, string> = {
    '正常': 'success',
    '迟到': 'warning',
    '早退': 'warning',
    '缺卡': 'danger',
    '旷工': 'danger',
    '请假': 'info',
    '出差': 'primary'
  }
  return (m[v] || 'info') as any
}

function shiftTagType(v: string) {
  const m: Record<string, string> = {
    '日班': 'primary',
    '早班': 'primary',
    '夜班': '',
    '中班': 'success',
    '常日班': 'info',
    '弹性班': 'warning',
    '休息': 'info'
  }
  return (m[v] || 'info') as any
}

function clockInClass(row: any) {
  if (!row.clock_in) return 'clock-time--missing'
  const clockInStatus = String(row.clock_in_status || '')
  const anomalyText = String(row.anomaly_type || '')
  const displayParts = statusParts(normalizeStatus(row))
  if (['迟到', '缺卡', '旷工'].includes(clockInStatus)) return 'clock-time--late'
  if (anomalyText.includes('迟到') || anomalyText.includes('旷工半天') || anomalyText.includes('缺少上班打卡')) return 'clock-time--late'
  if (displayParts.includes('迟到')) return 'clock-time--late'
  return 'clock-time--normal'
}

function clockOutClass(row: any) {
  if (!row.clock_out) return 'clock-time--missing'
  const clockOutStatus = String(row.clock_out_status || '')
  const anomalyText = String(row.anomaly_type || '')
  const displayParts = statusParts(normalizeStatus(row))
  if (['早退', '缺卡', '旷工'].includes(clockOutStatus)) return 'clock-time--late'
  if (anomalyText.includes('早退') || anomalyText.includes('缺少下班打卡')) return 'clock-time--late'
  if (displayParts.includes('早退')) return 'clock-time--late'
  return 'clock-time--normal'
}

// ============ 打卡记录 ============
const records = ref<any[]>([])
const recordLoading = ref(false)
const recordDate = ref<string[]>([])
const recordKeyword = ref('')
const recordStatusOptions = ['正常', '迟到', '早退', '缺卡', '旷工', '请假', '出差']
const recordStatuses = ref<string[]>([])
const recordDept = ref<number | null>(null)
const recordGroup = ref('')
const recordPage = reactive({ page: 1, page_size: 20, total: 0 })
const selectedRecordRows = ref<any[]>([])
const recordDetailVisible = ref(false)
const recordDetail = ref<any>(null)
const lastRecordTotal = ref(0)

function loadStatusOverrides() {
  localStorage.removeItem('attendanceStatusOverrides')
}

function normalizeStatus(row: any) {
  return composeRecordStatus(row)
}

function appendStatusPart(parts: string[], part: any) {
  const text = String(part || '').trim()
  if (text && !['-', '--', '正常'].includes(text) && !parts.includes(text)) parts.push(text)
}

function hasLocationStatus(row: any) {
  const anomalyText = String(row.anomaly_type || '')
  const locationText = [
    row.location_status,
    row.clock_in_location_status,
    row.clock_out_location_status,
    row.clock_in_location,
    row.clock_out_location,
  ].map((item) => String(item || '')).join(';')
  return Boolean(
    row.location_abnormal ||
    row.display_location_abnormal ||
    row.clock_in_location_abnormal ||
    row.clock_out_location_abnormal ||
    /超出打卡范围|打卡位置异常|不在打卡范围|异地打卡|模拟定位|定位异常|GPS异常|WiFi/.test(anomalyText) ||
    /打卡位置异常|不在打卡范围|异地打卡/.test(locationText)
  )
}

function statusPartsFromAnomaly(row: any) {
  const anomalyText = String(row.anomaly_type || '')
  const parts: string[] = []
  if (!anomalyText) return parts
  if (/缺少上班打卡|缺少下班打卡|缺卡/.test(anomalyText)) appendStatusPart(parts, '缺卡')
  if (/旷工半天|旷工/.test(anomalyText)) appendStatusPart(parts, '旷工')
  if (anomalyText.includes('工时不足')) appendStatusPart(parts, '异常')
  anomalyText.split(/[;；/、,，]/).forEach((item) => {
    const text = item.trim()
    if (text.startsWith('迟到')) appendStatusPart(parts, '迟到')
    if (text.startsWith('早退')) appendStatusPart(parts, '早退')
  })
  if (hasLocationStatus(row)) appendStatusPart(parts, '打卡位置异常')
  return parts
}

function composeRecordStatus(row: any) {
  const displayText = String(row.display_status || row.status || '').trim()
  if (['请假', '出差'].includes(displayText)) return displayText
  const parts: string[] = []
  statusParts(displayText).forEach((part) => appendStatusPart(parts, part))
  statusPartsFromAnomaly(row).forEach((part) => appendStatusPart(parts, part))
  if (['迟到', '缺卡', '旷工'].includes(String(row.clock_in_status || ''))) appendStatusPart(parts, row.clock_in_status)
  if (['早退', '缺卡', '旷工'].includes(String(row.clock_out_status || ''))) appendStatusPart(parts, row.clock_out_status)
  if (hasLocationStatus(row)) appendStatusPart(parts, '打卡位置异常')
  const priority = ['缺卡', '旷工', '迟到', '早退', '打卡位置异常', '异常', '待上班打卡', '待下班打卡']
  const ordered = priority.filter((part) => parts.includes(part))
  parts.forEach((part) => {
    if (!ordered.includes(part)) ordered.push(part)
  })
  return ordered.length ? ordered.join('/') : (displayText || '正常')
}

function statusClassName(statusText: string) {
  const parts = statusParts(statusText)
  if (parts.length > 1) return 'multi'
  const m: Record<string, string> = {
    正常: 'normal',
    迟到: 'late',
    早退: 'early',
    打卡位置异常: 'location',
    异常: 'abnormal',
    缺卡: 'missed',
    旷工: 'absent',
    请假: 'leave',
    出差: 'trip',
    申诉中: 'appealing',
  }
  return m[statusText] || 'normal'
}

function isExceptionStatus(statusText: string) {
  return statusParts(statusText).some((item) => ['迟到', '早退', '缺卡', '旷工', '打卡位置异常', '异常'].includes(item))
}

function statusParts(statusText: string) {
  return String(statusText || '')
    .split(/[\/,，、;；]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function recordRowClass({ row }: any) {
  return isExceptionStatus(row.display_status) ? 'record-row--exception' : ''
}

function handleRecordSelectionChange(rows: any[]) {
  selectedRecordRows.value = rows
}

function openRecordDetail(row: any) {
  recordDetail.value = row
  recordDetailVisible.value = true
}

function handleSingleAbnormal(row: any) {
  openRecordDetail(row)
}

function openRecordInAnomalyWorkbench(row: any) {
  recordDetailVisible.value = false
  activeTab.value = 'anomalies'
  appealSourceFilter.value = 'anomaly'
  appealStatusFilter.value = 'attendance_anomaly'
  void fetchAnomalies()
  ElMessage.info(`${row.employee_name || '员工'} ${row.date || ''} 的异常已切换到考勤审批列表`)
}

function markRecordAsNormal(row: any) {
  ElMessageBox.confirm(`确认将 ${row.employee_name} ${row.date} 标记为“正常”吗？`, '处理异常', {
    type: 'warning',
    confirmButtonText: '确认',
    cancelButtonText: '取消',
  }).then(async () => {
    await post(`/attendance/appeals/${row.id}/review`, { approved: true, note: '管理端处理异常，校准为正常' })
    ElMessage.success('已标记为正常')
    recordDetailVisible.value = false
    await fetchRecords()
    if (activeTab.value === 'anomalies') fetchAnomalies()
  }).catch((e: any) => {
    if (e && e !== 'cancel' && e !== 'close') {
      ElMessage.error(e?.response?.data?.detail || '处理异常失败')
    }
  })
}

function buildRecordQuery(page = recordPage.page, pageSize = recordPage.page_size) {
  const params: any = { page, page_size: pageSize }
  if (recordDate.value?.length === 2) {
    params.start_date = recordDate.value[0]
    params.end_date = recordDate.value[1]
  }
  if (recordKeyword.value) params.keyword = recordKeyword.value
  const selectedStatuses = recordStatuses.value.filter((status) => recordStatusOptions.includes(status))
  if (selectedStatuses.length) params.statuses = selectedStatuses.join(',')
  if (quickFilter.value === 'today_anomaly') params.anomalies_only = true
  if (recordDept.value) params.department_id = recordDept.value
  if (recordGroup.value) params.work_hour_type = recordGroup.value
  return params
}

function updateTrendAndStats(rows: any[]) {
  if (!rows.length) {
    todayStats.total = 0
    todayStats.present = 0
    todayStats.late = 0
    todayStats.leave = 0
    todayStats.outside = 0
    trendDays.value = []
    presentTrend.value = []
    lateTrend.value = []
    leaveTrend.value = []
    outsideTrend.value = []
    return
  }

  const grouped: Record<string, any[]> = {}
  for (const r of rows) {
    const d = r.date
    if (!grouped[d]) grouped[d] = []
    grouped[d].push(r)
  }
  const days = Object.keys(grouped).sort()
  const last7 = days.slice(-7)
  trendDays.value = last7.map((d) => d.slice(5))
  presentTrend.value = last7.map((d) => grouped[d].filter((x) => statusParts(normalizeStatus(x)).some((s) => ['正常', '迟到', '早退'].includes(s))).length)
  lateTrend.value = last7.map((d) => grouped[d].filter((x) => statusParts(normalizeStatus(x)).includes('迟到')).length)
  leaveTrend.value = last7.map((d) => grouped[d].filter((x) => x.status === '请假').length)
  outsideTrend.value = last7.map((d) => grouped[d].filter((x) => x.status === '出差').length)

  const latestDay = last7[last7.length - 1]
  const latestRows = grouped[latestDay] || []
  todayStats.total = latestRows.length
  todayStats.present = latestRows.filter((x) => statusParts(normalizeStatus(x)).some((s) => ['正常', '迟到', '早退'].includes(s))).length
  todayStats.late = latestRows.filter((x) => statusParts(normalizeStatus(x)).includes('迟到')).length
  todayStats.leave = latestRows.filter((x) => x.status === '请假').length
  todayStats.outside = latestRows.filter((x) => x.status === '出差').length
}

function parseApiDateTime(value?: string | null) {
  if (!value) return null
  const raw = String(value).trim()
  if (!raw) return null
  const normalized = raw.replace(' ', 'T')
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized)
  const parsed = new Date(hasTimezone ? normalized : `${normalized}Z`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function formatDateTime(value?: string | null) {
  if (!value) return ''
  const raw = String(value).trim()
  if (/^\d{4}-\d{1,2}-\d{1,2}$/.test(raw)) return raw
  const parsed = parseApiDateTime(raw)
  if (!parsed) return raw
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(parsed)
}

function formatApiClockTime(value?: string | null) {
  const parsed = parseApiDateTime(value)
  if (!parsed) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(parsed)
}

function formatPunchLocation(record: any) {
  const lat = record.clock_in_gps_lat ?? record.clock_out_gps_lat
  const lng = record.clock_in_gps_lng ?? record.clock_out_gps_lng
  if (lat === null || lat === undefined || lng === null || lng === undefined) return ''
  const latText = Number(lat).toFixed(6)
  const lngText = Number(lng).toFixed(6)
  return `GPS ${latText}, ${lngText}`
}

function ymdText(value: any) {
  const text = String(value || '').trim()
  const match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/)
  if (match) return `${match[1]}-${match[2].padStart(2, '0')}-${match[3].padStart(2, '0')}`
  const d = new Date(text)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function weekdayOfYmd(value: string) {
  const match = ymdText(value).match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return 1
  const d = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  const day = d.getDay()
  return day === 0 ? 7 : day
}

function dateOrdinal(value: any) {
  const text = ymdText(value)
  if (!text) return 0
  const [y, m, d] = text.split('-').map(Number)
  return new Date(y, m - 1, d).getTime()
}

function intArray(raw: any) {
  return Array.isArray(raw)
    ? raw.map((x: any) => Number(x)).filter((x: number) => Number.isFinite(x))
    : []
}

function hmForCalc(value: any) {
  const text = normalizeTime(value)
  return /^\d{2}:\d{2}$/.test(text) ? text : ''
}

function isEvenWeekFrom(anchorText: string, targetText: string) {
  const anchor = new Date(dateOrdinal(anchorText))
  const target = new Date(dateOrdinal(targetText))
  if (Number.isNaN(anchor.getTime()) || Number.isNaN(target.getTime())) return true
  const anchorMonday = new Date(anchor)
  anchorMonday.setDate(anchor.getDate() - ((anchor.getDay() + 6) % 7))
  const targetMonday = new Date(target)
  targetMonday.setDate(target.getDate() - ((target.getDay() + 6) % 7))
  const diffWeeks = Math.floor((targetMonday.getTime() - anchorMonday.getTime()) / (7 * 24 * 60 * 60 * 1000))
  return Math.abs(diffWeeks % 2) === 0
}

function ruleSpecificity(rule: any, emp: any, employeeId: number) {
  const extra = rule.extra || {}
  const assignedIds = intArray(extra.assigned_employee_ids)
  const memberIds = intArray(extra.member_ids)
  if (assignedIds.includes(employeeId) || memberIds.includes(employeeId)) return 400
  const selected = Array.isArray(extra.scope_custom_types) ? extra.scope_custom_types.map(String) : []
  const deptIds = intArray(extra.department_ids)
  if (selected.some((x: string) => ['group', 'tag', 'position'].includes(x))) return 300
  if (selected.includes('department') || deptIds.length || (rule.department_id && Number(rule.department_id) === Number(emp.department_id))) return 200
  if ((extra.scope_mode || 'all') === 'all' || (extra.scope_type || 'all') === 'all') return 100
  return 0
}

function ruleMatchesRecord(rule: any, record: any, emp: any) {
  const employeeId = Number(record.employee_id)
  if (!rule || rule.is_active === false || !employeeId) return false
  const recordDate = ymdText(record.date)
  if (ymdText(rule.effective_date) && ymdText(rule.effective_date) > recordDate) return false
  if (rule.department_id && Number(rule.department_id) !== Number(emp.department_id)) return false
  if (rule.location_id && Number(rule.location_id) !== Number(emp.location_id)) return false

  const extra = rule.extra || {}
  if (intArray(extra.exclude_employee_ids).includes(employeeId)) return false
  const assignedIds = intArray(extra.assigned_employee_ids)
  if (assignedIds.length && !assignedIds.includes(employeeId)) return false
  if (assignedIds.includes(employeeId)) return true

  const scopeMode = String(extra.scope_mode || 'all')
  if (scopeMode === 'all') return true
  const selected = Array.isArray(extra.scope_custom_types) ? extra.scope_custom_types.map(String) : []
  if (selected.includes('employee') && intArray(extra.member_ids).includes(employeeId)) return true
  if (selected.includes('department')) {
    const deptIds = intArray(extra.department_ids)
    return deptIds.includes(Number(emp.department_id)) || Number(rule.department_id) === Number(emp.department_id)
  }
  return false
}

function resolveRecordRule(record: any, emp: any) {
  const recordDate = ymdText(record.date)
  const employeeId = Number(record.employee_id)
  const matches = rules.value
    .filter((rule: any) => ruleMatchesRecord(rule, record, emp))
    .sort((a: any, b: any) => {
      const priorityDiff = Number(a.priority || 100) - Number(b.priority || 100)
      if (priorityDiff) return priorityDiff
      const specDiff = ruleSpecificity(b, emp, employeeId) - ruleSpecificity(a, emp, employeeId)
      if (specDiff) return specDiff
      const effectiveDiff = dateOrdinal(b.effective_date || recordDate) - dateOrdinal(a.effective_date || recordDate)
      if (effectiveDiff) return effectiveDiff
      return Number(a.id || 0) - Number(b.id || 0)
    })
  return matches[0] || null
}

function segmentAppliesToDate(segment: any, rule: any, recordDate: string) {
  const weekday = weekdayOfYmd(recordDate)
  if (segment?.biweekly_enabled) {
    const selected = isEvenWeekFrom(ymdText(rule.effective_date) || recordDate, recordDate)
      ? intArray(segment.this_week_weekdays)
      : intArray(segment.next_week_weekdays)
    return selected.includes(weekday)
  }
  return intArray(segment?.weekdays).includes(weekday)
}

function resolveRuleSegment(rule: any, recordDate: string) {
  const segments = Array.isArray(rule?.extra?.time_segments) ? rule.extra.time_segments : []
  return segments.find((segment: any) => segmentAppliesToDate(segment, rule, recordDate)) || segments[0] || null
}

function restPeriodsFromSegment(segment: any) {
  const rows = Array.isArray(segment?.rest_periods)
    ? segment.rest_periods
    : []
  const periods = rows
    .map((row: any) => ({ start: hmForCalc(row?.start), end: hmForCalc(row?.end) }))
    .filter((row: any) => row.start && row.end)
  if (periods.length) return periods
  const restStart = hmForCalc(segment?.rest_start)
  const restEnd = hmForCalc(segment?.rest_end)
  return restStart && restEnd ? [{ start: restStart, end: restEnd }] : []
}

function intervalMinutes(startHm: string, endHm: string) {
  const start = hmToMinutes(startHm)
  let end = hmToMinutes(endHm)
  if (end <= start) end += 24 * 60
  return { start, end, minutes: Math.max(0, end - start) }
}

function expectedHoursFromRule(rule: any, recordDate: string) {
  const extra = rule?.extra || {}
  if (extra.rule_type === 'free') {
    const mode = String(extra.free_work_hours_mode || '')
    if (mode.startsWith('limit:')) return Math.max(0, Math.floor(Number(mode.slice(6)) + 0.5))
    return 0
  }

  const segment = resolveRuleSegment(rule, recordDate)
  const clockIn = hmForCalc(segment?.clock_in || rule?.clock_in_time)
  const clockOut = hmForCalc(segment?.clock_out || rule?.clock_out_time)
  if (!clockIn || !clockOut) return null
  const work = intervalMinutes(clockIn, clockOut)
  let minutes = work.minutes
  for (const period of restPeriodsFromSegment(segment)) {
    const rest = intervalMinutes(period.start, period.end)
    const overlapStart = Math.max(work.start, rest.start)
    const overlapEnd = Math.min(work.end, rest.end)
    if (overlapEnd > overlapStart) minutes -= overlapEnd - overlapStart
  }
  return Math.max(0, Math.floor(minutes / 60 + 0.5))
}

function expectedHoursForRecord(record: any, emp: any) {
  if (record.expected_hours !== null && record.expected_hours !== undefined && record.expected_hours !== '') {
    const backendValue = Number(record.expected_hours)
    if (Number.isFinite(backendValue)) return backendValue
  }
  if (['请假', '出差'].includes(record.status)) return 0
  const rule = resolveRecordRule(record, emp)
  const computed = expectedHoursFromRule(rule, ymdText(record.date))
  return computed === null ? 0 : computed
}

async function fetchRecords() {
  recordLoading.value = true
  try {
    const params = buildRecordQuery(recordPage.page, recordPage.page_size)
    if (!rules.value.length) await fetchRules()
    const res = await get('/attendance/records', params)
    const list: any[] = res.items || res.data || []
    const mappedRows = list.map((r: any) => {
      const emp = employeeMap.value[r.employee_id] || {}
      const dept = flatDeptOptions.value.find((d) => d.id === emp.department_id)?.name || ''
      const sourceText = r.source === 'gate' ? '考勤机' : r.source === 'manual' ? '管理员补录' : r.source === 'app' ? 'APP' : (r.source || '-')
      const expectedHours = expectedHoursForRecord(r, emp)
      const punchLocation = r.clock_out_location || r.clock_in_location || formatPunchLocation(r)
      const punchLocationSource = r.clock_out_location ? 'clock_out' : (r.clock_in_location ? 'clock_in' : 'gps')
      const punchLocationAbnormal = punchLocationSource === 'clock_out'
        ? Boolean(r.clock_out_location_abnormal)
        : punchLocationSource === 'clock_in'
          ? Boolean(r.clock_in_location_abnormal)
          : Boolean(r.location_abnormal)
      const hasGps = Boolean(r.clock_in_gps_lat && r.clock_in_gps_lng) || Boolean(r.clock_out_gps_lat && r.clock_out_gps_lng)
      const wifiText = r.clock_in_wifi_ssid || r.clock_out_wifi_ssid || (hasGps ? 'GPS定位' : '-')
      const deviceText = r.clock_out_device || r.clock_in_device || sourceText
      return {
        ...r,
        employee_name: r.employee_name || emp.name || '',
        employee_no: emp.employee_no || '',
        department_name: dept,
        clock_in: formatApiClockTime(r.clock_in_time),
        clock_out: formatApiClockTime(r.clock_out_time),
        expected_hours: expectedHours.toFixed(1),
        actual_hours: Number(r.work_hours || 0).toFixed(1),
        device_info: `${deviceText} / ${wifiText}`,
        clock_in_location: punchLocation,
        display_location_abnormal: punchLocationAbnormal,
      }
    })

    for (const row of mappedRows) {
      row.display_status = normalizeStatus(row)
    }

    recordPage.total = res.total || 0
    if (lastRecordTotal.value > 0 && recordPage.total > lastRecordTotal.value) {
      ElMessage.info(`有 ${recordPage.total - lastRecordTotal.value} 条新打卡记录`)
    }
    lastRecordTotal.value = recordPage.total
    records.value = mappedRows

    const allRes = await get('/attendance/records', buildRecordQuery(1, 999))
    const allRows = allRes.items || allRes.data || []
    updateTrendAndStats(allRows)
  } catch { records.value = [] }
  finally { recordLoading.value = false }
}

function handleSearch() {
  recordPage.page = 1
  fetchRecords()
}

function resetRecordFilters() {
  recordDate.value = []
  recordKeyword.value = ''
  recordStatuses.value = []
  recordDept.value = null
  recordGroup.value = ''
  quickFilter.value = 'all'
  handleSearch()
}

function manualRefresh() {
  fetchRecords()
  fetchTodayStats()
  ElMessage.success('已刷新')
}

function applyQuickFilter(type: 'all' | 'today_anomaly' | 'week_late' | 'pending_appeal') {
  quickFilter.value = type
  const today = new Date()
  const toDate = (d: Date) => d.toISOString().split('T')[0]
  if (type === 'all') {
    resetRecordFilters()
    return
  }
  if (type === 'today_anomaly') {
    const t = toDate(today)
    recordDate.value = [t, t]
    recordStatuses.value = []
  } else if (type === 'week_late') {
    const from = new Date(today)
    from.setDate(from.getDate() - 6)
    recordDate.value = [toDate(from), toDate(today)]
    recordStatuses.value = ['迟到']
  } else if (type === 'pending_appeal') {
    recordStatuses.value = []
  }
  handleSearch()
}

function statusFromRows(rows: any[]) {
  return rows.filter((r) => isExceptionStatus(r.display_status))
}

function downloadCsv(filename: string, headers: string[], rows: any[][]) {
  const csv = [headers.join(','), ...rows.map((r) => r.map((x) => `"${String(x ?? '').replace(/"/g, '""')}"`).join(','))].join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function exportCurrentList() {
  const rows = records.value.map((r) => [r.employee_name, r.employee_no, r.department_name, r.date, r.clock_in, r.clock_out, r.expected_hours, r.actual_hours, composeRecordStatus(r), r.clock_in_location || '-', r.device_info])
  downloadCsv(`attendance_current_${new Date().toISOString().slice(0, 10)}.csv`, ['员工姓名', '工号', '部门', '日期', '上班打卡', '下班打卡', '应出勤工时', '实际工时', '状态', '打卡位置', '打卡设备/IP'], rows)
}

function exportExceptionList() {
  const exceptionRows = statusFromRows(records.value)
  const rows = exceptionRows.map((r) => [r.employee_name, r.employee_no, r.department_name, r.date, composeRecordStatus(r), r.anomaly_type || '-', r.clock_in_location || '-'])
  downloadCsv(`attendance_anomaly_${new Date().toISOString().slice(0, 10)}.csv`, ['员工姓名', '工号', '部门', '日期', '异常类型', '异常说明', '打卡位置'], rows)
}

function handleExportCommand(command: string) {
  if (command === 'monthly') exportMonthlyReport()
  if (command === 'current') exportCurrentList()
  if (command === 'anomaly') exportExceptionList()
}

async function batchMarkAsNormal() {
  try {
    await Promise.all(
      selectedRecordRows.value.map((r) => post(`/attendance/appeals/${r.id}/review`, { approved: true, note: '管理端批量处理异常，校准为正常' }))
    )
    ElMessage.success(`已批量标记 ${selectedRecordRows.value.length} 条为正常`)
    await fetchRecords()
    if (activeTab.value === 'anomalies') fetchAnomalies()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '批量标记失败')
  }
}

function batchApproveAppeal() {
  Promise.all(
    selectedRecordRows.value.map((r) => post(`/attendance/appeals/${r.id}/review`, { approved: true, note: '批量通过' }))
  ).then(async () => {
    ElMessage.success('已批量通过申诉')
    await fetchRecords()
    if (activeTab.value === 'anomalies') fetchAnomalies()
  }).catch(() => ElMessage.error('批量通过失败'))
}

function batchExportAnomalies() {
  const rows = statusFromRows(selectedRecordRows.value.length ? selectedRecordRows.value : records.value)
  if (!rows.length) {
    ElMessage.warning('没有可导出的异常记录')
    return
  }
  const data = rows.map((r) => [r.employee_name, r.department_name, r.date, composeRecordStatus(r), r.anomaly_type || '-'])
  downloadCsv(`attendance_exception_batch_${new Date().toISOString().slice(0, 10)}.csv`, ['员工姓名', '部门', '日期', '状态', '异常说明'], data)
}

// ============ 考勤规则 ============
const rules = ref<any[]>([])
const ruleLoading = ref(false)
const ruleDialogVisible = ref(false)
const assigneePickerVisible = ref(false)
const assigneeTreeRef = ref<any>(null)
const assigneeKeyword = ref('')
const noPunchSelectorVisible = ref(false)
const noPunchTreeRef = ref<any>(null)
const noPunchKeyword = ref('')
const reportTargetSelectorVisible = ref(false)
const reportTargetTreeRef = ref<any>(null)
const reportTargetKeyword = ref('')
const assistManagerSelectorVisible = ref(false)
const assistManagerTreeRef = ref<any>(null)
const assistManagerKeyword = ref('')
const ruleSubmitting = ref(false)
const ruleStatusFilter = ref<boolean | null>(null)
const ruleLogsVisible = ref(false)
const ruleLogs = ref<any[]>([])
const currentRuleName = ref('')
const ruleFormRef = ref<FormInstance>()
const expandedRuleScopeIds = ref<Set<string | number>>(new Set())
const ruleTypeOptions = [
  { value: 'fixed', label: '固定时间上下班', desc: '所有人按相同时间打卡，适用于办公室坐班。' },
  { value: 'shift', label: '按排班上下班', desc: '按班次结果或自选班次打卡，适用于门店/流水线。' },
  { value: 'free', label: '自由上下班', desc: '不固定时间，可随时打卡只统计工时。' },
]
const weekdayOptions = [
  { label: '周一', value: 1 },
  { label: '周二', value: 2 },
  { label: '周三', value: 3 },
  { label: '周四', value: 4 },
  { label: '周五', value: 5 },
  { label: '周六', value: 6 },
  { label: '周日', value: 7 },
]

type AssigneeTreeNode = {
  key: string
  type: 'company' | 'department' | 'employee'
  label: string
  employeeNo?: string
  memberCount?: number
  searchText?: string
  children?: AssigneeTreeNode[]
}

type MemberPickerKind = 'no_punch' | 'report_target' | 'assist_manager'

const memberPickerFieldMap: Record<MemberPickerKind, 'no_punch_employee_ids' | 'report_target_ids' | 'assist_manager_ids'> = {
  no_punch: 'no_punch_employee_ids',
  report_target: 'report_target_ids',
  assist_manager: 'assist_manager_ids',
}

function employeeDisplayName(employee: any) {
  const no = String(employee?.employee_no || '').trim()
  return no ? `${employee?.name || `员工#${employee?.id}`}(${no})` : (employee?.name || `员工#${employee?.id}`)
}

function normalizedMemberIds(kind: MemberPickerKind): number[] {
  const field = memberPickerFieldMap[kind]
  return Array.from(new Set<number>(
    (ruleForm.value.extra?.[field] || [])
      .map((id: any) => Number(id))
      .filter((id: number) => Number.isFinite(id))
  ))
}

function setMemberPickerIds(kind: MemberPickerKind, ids: number[]) {
  const field = memberPickerFieldMap[kind]
  if (!ruleForm.value.extra) ruleForm.value.extra = defaultRuleExtra()
  ruleForm.value.extra[field] = Array.from(new Set(ids.filter((id) => Number.isFinite(Number(id)))))
}

function selectedMemberEmployees(kind: MemberPickerKind) {
  const selected = new Set<number>(normalizedMemberIds(kind))
  return (employeeOptions.value || []).filter((employee: any) => selected.has(Number(employee.id)))
}

const selectedAssignedEmployees = computed(() => {
  const selected = new Set<number>((ruleForm.value.extra.assigned_employee_ids || []).map((id: any) => Number(id)))
  return (employeeOptions.value || []).filter((employee: any) => selected.has(Number(employee.id)))
})

function normalizeSelectableAssigneeIds(ids: any[]) {
  const selectable = new Set<number>(
    (employeeOptions.value || [])
      .map((employee: any) => Number(employee.id))
      .filter((id: number) => Number.isFinite(id))
  )
  const hasSelectableSource = selectable.size > 0
  return Array.from(new Set(
    (ids || [])
      .map((id: any) => Number(id))
      .filter((id: number) => Number.isFinite(id) && (!hasSelectableSource || selectable.has(id)))
  ))
}

const selectedNoPunchEmployees = computed(() => selectedMemberEmployees('no_punch'))
const selectedReportTargetEmployees = computed(() => selectedMemberEmployees('report_target'))
const selectedAssistManagerEmployees = computed(() => selectedMemberEmployees('assist_manager'))

const assignedSummaryText = computed(() => {
  const count = selectedAssignedEmployees.value.length
  return count ? `已选择 ${count} 人` : '请选择打卡人员'
})

function assigneeEmpNodeKey(employeeId: number) {
  return `emp-${employeeId}`
}

function assigneeDeptNodeKey(departmentId: number) {
  return `dept-${departmentId}`
}

function assigneeCompanyNodeKey(companyId: number) {
  return `company-${companyId}`
}

const assigneeTreeData = computed<AssigneeTreeNode[]>(() => {
  const groupedEmployees = new Map<number, any[]>()
  const unassignedEmployees: any[] = []
  for (const employee of (employeeOptions.value || [])) {
    const departmentId = Number(employee?.department_id)
    if (Number.isFinite(departmentId) && departmentId > 0) {
      const list = groupedEmployees.get(departmentId) || []
      list.push(employee)
      groupedEmployees.set(departmentId, list)
    } else {
      unassignedEmployees.push(employee)
    }
  }
  groupedEmployees.forEach((employees) => {
    employees.sort((a: any, b: any) => String(a.employee_no || a.name || '').localeCompare(String(b.employee_no || b.name || ''), 'zh-Hans-CN'))
  })
  unassignedEmployees.sort((a: any, b: any) => String(a.employee_no || a.name || '').localeCompare(String(b.employee_no || b.name || ''), 'zh-Hans-CN'))

  const employeeNode = (employee: any): AssigneeTreeNode => ({
    key: assigneeEmpNodeKey(Number(employee.id)),
    type: 'employee',
    label: String(employee.name || `员工#${employee.id}`),
    employeeNo: employee.employee_no ? String(employee.employee_no) : '',
    memberCount: 1,
    searchText: `${employee.name || ''} ${employee.employee_no || ''} ${employee.position || ''}`.toLowerCase(),
  })

  const summarizeNode = (node: AssigneeTreeNode): AssigneeTreeNode => {
    if (node.type === 'employee') return node
    const children = (node.children || []).map(summarizeNode)
    const memberCount = children.reduce((sum, child) => sum + (child.memberCount || 0), 0)
    return {
      ...node,
      children,
      memberCount,
      searchText: [node.label, ...children.map((child) => child.searchText || child.label)].join(' ').toLowerCase(),
    }
  }

  const buildDepartmentNode = (department: any): AssigneeTreeNode => {
    const children: AssigneeTreeNode[] = []
    for (const subDepartment of (department.children || [])) {
      children.push(buildDepartmentNode(subDepartment))
    }
    const departmentId = Number(department.id)
    for (const employee of (groupedEmployees.get(departmentId) || [])) {
      children.push(employeeNode(employee))
    }
    groupedEmployees.delete(departmentId)
    return {
      key: assigneeDeptNodeKey(departmentId),
      type: 'department',
      label: String(department.name || `部门#${department.id}`),
      children,
      searchText: String(department.name || '').toLowerCase(),
    }
  }

  const buildDepartmentRoots = () => {
    const map = new Map<number, any>()
    for (const department of (deptOptions.value || [])) {
      map.set(Number(department.id), { ...department, children: [] })
    }
    const roots: any[] = []
    for (const department of (deptOptions.value || [])) {
      const node = map.get(Number(department.id))
      const parentId = Number(department.parent_id)
      if (Number.isFinite(parentId) && map.has(parentId)) {
        map.get(parentId).children.push(node)
      } else {
        roots.push(node)
      }
    }
    const sortDepartments = (items: any[]) => {
      items.sort((a: any, b: any) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN'))
      items.forEach((item: any) => sortDepartments(item.children || []))
    }
    sortDepartments(roots)
    return roots
  }

  const departmentRoots = buildDepartmentRoots()
  const departmentNodes = departmentRoots.map((department: any) => buildDepartmentNode(department))
  const companyLayoutMap = new Map<number, any>()
  ;(assigneeCompanyLayouts.value || []).forEach((layout: any) => {
    companyLayoutMap.set(Number(layout.company_id), layout)
  })
  const companyMap = new Map<number, AssigneeTreeNode & { parentCompanyId?: number | null }>()
  for (const company of (assigneeCompanies.value || [])) {
    const companyId = Number(company.id)
    if (!Number.isFinite(companyId)) continue
    const layout = companyLayoutMap.get(companyId)
    if (layout?.is_visible === false) continue
    const parentCompanyId = Number(layout?.parent_company_id ?? company.parent_company_id)
    companyMap.set(companyId, {
      key: assigneeCompanyNodeKey(companyId),
      type: 'company',
      label: String(company.name || `公司#${companyId}`),
      children: [],
      parentCompanyId: Number.isFinite(parentCompanyId) ? parentCompanyId : null,
    })
  }

  const companyRoots: Array<AssigneeTreeNode & { parentCompanyId?: number | null }> = []
  companyMap.forEach((node) => {
    if (node.parentCompanyId && companyMap.has(node.parentCompanyId)) {
      companyMap.get(node.parentCompanyId)?.children?.push(node)
    } else {
      companyRoots.push(node)
    }
  })

  const orphanDepartmentNodes: AssigneeTreeNode[] = []
  departmentRoots.forEach((department: any, index: number) => {
    const companyId = Number(department.company_id)
    const departmentNode = departmentNodes[index]
    if (Number.isFinite(companyId) && companyMap.has(companyId)) {
      companyMap.get(companyId)?.children?.push(departmentNode)
    } else {
      orphanDepartmentNodes.push(departmentNode)
    }
  })

  const nodes = companyRoots.length
    ? [...companyRoots, ...orphanDepartmentNodes]
    : departmentNodes
  groupedEmployees.forEach((employees, departmentId) => {
    if (!employees.length) return
    const departmentName = employees.find((employee: any) => employee?.department_name)?.department_name
    nodes.push({
      key: `dept-detached-${departmentId}`,
      type: 'department',
      label: String(departmentName || `未同步部门#${departmentId}`),
      children: employees.map(employeeNode),
    })
  })
  if (unassignedEmployees.length) {
    nodes.push({
      key: 'dept-unassigned',
      type: 'department',
      label: '未分配部门',
      children: unassignedEmployees.map(employeeNode),
    })
  }
  return nodes.map(summarizeNode)
})

const assigneeDefaultExpandedKeys = computed(() => {
  const keys: string[] = []
  const walk = (nodes: AssigneeTreeNode[], depth = 0) => {
    for (const node of nodes || []) {
      if (node.children?.length && depth < 2) {
        keys.push(node.key)
        walk(node.children, depth + 1)
      }
    }
  }
  walk(assigneeTreeData.value)
  return keys
})

const officialHolidayPlan: Record<number, {
  holidays: Array<{ name: string, start: string, end: string }>
  makeupWorkdays: string[]
}> = {
  // 国办发明电〔2024〕12号
  2025: {
    holidays: [
      { name: '元旦', start: '2025-01-01', end: '2025-01-01' },
      { name: '春节', start: '2025-01-28', end: '2025-02-04' },
      { name: '清明节', start: '2025-04-04', end: '2025-04-06' },
      { name: '劳动节', start: '2025-05-01', end: '2025-05-05' },
      { name: '端午节', start: '2025-05-31', end: '2025-06-02' },
      { name: '国庆节/中秋节', start: '2025-10-01', end: '2025-10-08' },
    ],
    makeupWorkdays: ['2025-01-26', '2025-02-08', '2025-04-27', '2025-09-28', '2025-10-11'],
  },
  // 国办发明电〔2025〕7号
  2026: {
    holidays: [
      { name: '元旦', start: '2026-01-01', end: '2026-01-03' },
      { name: '春节', start: '2026-02-15', end: '2026-02-23' },
      { name: '清明节', start: '2026-04-04', end: '2026-04-06' },
      { name: '劳动节', start: '2026-05-01', end: '2026-05-05' },
      { name: '端午节', start: '2026-06-19', end: '2026-06-21' },
      { name: '中秋节', start: '2026-09-25', end: '2026-09-27' },
      { name: '国庆节', start: '2026-10-01', end: '2026-10-07' },
    ],
    makeupWorkdays: ['2026-02-14', '2026-02-28', '2026-05-09', '2026-09-20', '2026-10-10'],
  },
}
function normalizeHolidayCalendarTag(tag: unknown) {
  return String(tag || '').trim().replace(/调休上班/g, '补班')
}

const holidayPresetMap: Record<number, {
  workdays: string[]
  restdays: string[]
  tags: Record<string, string>
}> = Object.fromEntries(
  Object.entries(officialHolidayPlan).map(([yearText, plan]) => {
    const year = Number(yearText)
    const workdays = [...plan.makeupWorkdays]
    const restdays: string[] = []
    const tags: Record<string, string> = {}
    for (const item of plan.holidays) {
      const start = new Date(`${item.start}T00:00:00`)
      const end = new Date(`${item.end}T00:00:00`)
      for (let t = start.getTime(); t <= end.getTime(); t += 24 * 3600 * 1000) {
        const d = new Date(t)
        const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
        restdays.push(key)
        tags[key] = item.name
      }
    }
    for (const d of workdays) tags[d] = '补班'
    return [year, { workdays, restdays, tags }]
  })
)
const locationDialogVisible = ref(false)
const wifiDialogVisible = ref(false)
const locationDraft = ref({
  country: '中国大陆',
  name: '',
  radius: 300,
  latitude: '',
  longitude: '',
})
const wifiDraft = ref({ name: '', bssid: '' })
const wifiBssidParts = ref<string[]>(['', '', '', '', '', ''])
const locationSuggestionToken = ref(0)
const segmentEditorVisible = ref(false)
const editingSegmentIndex = ref(-1)
const segmentDraft = ref<any>(defaultTimeSegment())
const restTimeDialogVisible = ref(false)
const punchWindowDialogVisible = ref(false)
const halfDayDialogVisible = ref(false)
const copySegmentDialogVisible = ref(false)
const copySegmentSelectedKeys = ref<string[]>([])
const restTimeDraft = ref<Array<{ start: string, end: string }>>([{ start: '', end: '' }])
const punchWindowDraft = ref<Array<{
  title: string
  clock_in: string
  clock_out: string
  in_start: string
  in_end: string
  out_start: string
  out_end: string
  removable: boolean
}>>([])
const halfDayDraft = ref<{ am: string[], pm: string[] }>({ am: ['09:00', '13:00'], pm: ['13:00', '17:00'] })
const workCalendarPreviewVisible = ref(false)
const workCalendarPreviewMonth = ref(new Date().toISOString().slice(0, 7))
const holidayCalendarVisible = ref(false)
const nowDate = new Date()
const holidayCalendarYear = ref(nowDate.getFullYear())
const holidayCalendarMonth = ref(nowDate.getMonth() + 1)
const holidayCalendarLoading = ref(false)
const holidayCalendarServerMap = ref<Record<string, { dayType: 'work' | 'rest', tag: string }>>({})
const specialDateDialogVisible = ref(false)
const specialDateMode = ref<'must' | 'no'>('must')
const specialDateDraft = ref<any>({
  mode: 'single',
  date: '',
  date_range: [] as string[],
  repeat: 'none',
  punch_times: [{ start: '09:00', end: '18:00' }],
  reason: '',
})
const shiftTemplateDialogVisible = ref(false)
const shiftTemplateDraft = ref({ name: '', clock_in: '09:30', clock_out: '18:30' })
const restdaySettingDialogVisible = ref(false)
const restdaySettingDraft = ref({
  shift_mode: 'multi',
  interval_minutes: 5,
})
const leavePunchSettingDialogVisible = ref(false)
const leavePunchSettingDraft = ref({
  need_punch: false,
  before_max_advance: '无限制',
  after_max_delay: '无限制',
})
const leavePunchSettingSnapshot = ref('')
const overtimeRuleDialogVisible = ref(false)
const overtimeDurationDialogVisible = ref(false)
const overtimeRuleActiveTab = ref<'workday' | 'restday' | 'holiday'>('workday')
const overtimeRuleDraft = ref<any>(null)
const overtimeDurationDraft = ref<any>(null)
const restdayIntervalOptions = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
const leavePunchTimeLimitOptions = ['无限制', ...Array.from({ length: 18 }, (_, idx) => `${(idx + 1) * 10}分钟`)]
// 与后端 PunchCorrectionEligibilityService 的补卡类型保持一致。
const patchTypeOptions = ['缺卡/旷工', '迟到', '早退', '其他异常（地点/设备异常）', '正常']
const patchTimeLimitOptions = ['无限制', ...Array.from({ length: 31 }, (_, idx) => `过去${idx + 1}天内`)]
const patchMonthLimitOptions = ['无限制', ...Array.from({ length: 31 }, (_, idx) => `${idx + 1}次`)]
const patchDeadlineOptions = ['不设置', ...Array.from({ length: 31 }, (_, idx) => `下月${idx + 1}日`)]
const timezoneOptions = [
  { value: 'Asia/Shanghai', label: '(GMT+08:00) 中国标准时间 - 北京' },
]
const mobileRemindOnDutyOptions = ['不提醒', '前5分钟', '前10分钟', '前15分钟', '准点']
const mobileRemindOffDutyOptions = ['不提醒', '准点', '后10分钟', '后30分钟']
const overtimeDayTabs: Array<{value:'workday'|'restday'|'holiday';label:string}> = [
  { value: 'workday', label: '工作日' },
  { value: 'restday', label: '休息日' },
  { value: 'holiday', label: '节假日' },
]
const overtimeCalcMethodOptions = [
  { value: 'by_approval', label: '按加班审批时长计算', desc: '加班审批通过后，按审批时长计算加班时长' },
  { value: 'by_clock', label: '按打卡时长计算', desc: '根据打卡时间自动计算加班时长' },
  { value: 'by_approval_clock', label: '按审批和打卡时长计算', desc: '在加班审批时段内，按打卡时间核算加班时长' },
]
const shiftArrangeDialogVisible = ref(false)
const shiftClassDrawerVisible = ref(false)
const shiftClassEditorVisible = ref(false)
const shiftClassSubmitting = ref(false)
const shiftClassEditingIndex = ref<number>(-1)
const shiftClassDraft = ref<any>(defaultShiftTemplate({ name: '早班', clock_in: '08:00', clock_out: '16:00' }))
const shiftArrangeMonth = ref(new Date().toISOString().slice(0, 7))
const shiftArrangeKeyword = ref('')
const shiftArrangeLoading = ref(false)
const shiftAssignMap = ref<Record<string, string>>({})
const shiftAssignSavingKeys = ref<Set<string>>(new Set())
const shiftMatchMinuteOptions = [0, 15, 30, 45, 60, 90, 120, 150, 180, 240, 300]

function defaultShiftTemplate(seed?: Partial<any>) {
  const baseIn = String(seed?.clock_in || '09:00')
  const baseOut = String(seed?.clock_out || '17:00')
  return {
    id: String(seed?.id || `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`),
    name: String(seed?.name || '班次'),
    color: String(seed?.color || '#93C5FD'),
    clock_in: baseIn,
    clock_out: baseOut,
    check_in_required: seed?.check_in_required !== false,
    check_out_required: seed?.check_out_required !== false,
    rest_periods: Array.isArray(seed?.rest_periods) ? seed?.rest_periods : [],
    rest_start: String(seed?.rest_start || ''),
    rest_end: String(seed?.rest_end || ''),
    punch_start: String(seed?.punch_start || minutesToHm(hmToMinutes(baseIn) - 300)),
    punch_end: String(seed?.punch_end || minutesToHm(hmToMinutes(baseOut) + 240)),
    flex_mode: String(seed?.flex_mode || 'none'),
    late_leave_next_day: !!seed?.late_leave_next_day,
    half_day_am: Array.isArray(seed?.half_day_am) ? seed?.half_day_am : [baseIn, minutesToHm(hmToMinutes(baseIn) + 240)],
    half_day_pm: Array.isArray(seed?.half_day_pm) ? seed?.half_day_pm : [minutesToHm(hmToMinutes(baseOut) - 240), baseOut],
    extra_periods: Array.isArray(seed?.extra_periods) ? seed?.extra_periods : [],
    punch_windows: Array.isArray(seed?.punch_windows) ? seed?.punch_windows : [],
  }
}

function defaultShiftTemplatePreset() {
  return [
    defaultShiftTemplate({ name: '休息', clock_in: '00:00', clock_out: '00:00', color: '#E5E7EB', check_in_required: false, check_out_required: false }),
    defaultShiftTemplate({ name: '早班', clock_in: '08:00', clock_out: '16:00', color: '#BFDBFE' }),
    defaultShiftTemplate({ name: '晚班', clock_in: '16:00', clock_out: '00:00', color: '#FCD34D', punch_end: '03:59' }),
  ]
}

function defaultTimeSegment(seed?: Partial<any>) {
  return {
    _key: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    name: '工作时段1',
    weekdays: [1, 2, 3, 4, 5],
    biweekly_enabled: false,
    this_week_weekdays: [1, 2, 3, 4, 5],
    next_week_weekdays: [1, 2, 3, 4, 5],
    clock_in: '09:00',
    clock_out: '17:00',
    check_in_required: true,
    check_out_required: true,
    extra_periods: [] as Array<{ clock_in: string, clock_out: string, check_in_required: boolean, check_out_required: boolean }>,
    rest_periods: [] as Array<{ start: string, end: string }>,
    punch_windows: [] as Array<{ in_start: string, in_end: string, out_start: string, out_end: string }>,
    rest_start: '',
    rest_end: '',
    punch_start: '04:00',
    punch_end: '03:59',
    flex_mode: 'none',
    late_leave_next_day: false,
    half_day_am: ['09:00', '13:00'],
    half_day_pm: ['13:00', '17:00'],
    ...seed,
  }
}

function buildSegmentName(index: number) {
  return `工作时段${index + 1}`
}

function defaultSpecialDateConfig(seed?: Partial<any>) {
  return {
    mode: 'single',
    date: '',
    date_range: [] as string[],
    repeat: 'none',
    punch_times: [{ start: '09:00', end: '18:00' }],
    reason: '',
    ...seed,
  }
}

function defaultOvertimeDayRule(seed?: Partial<any>) {
  return {
    enabled: true,
    period_mode: 'all',
    calc_method: 'by_approval',
    start_after_off_duty_minutes: 0,
    min_minutes: 30,
    max_minutes: 240,
    allow_rest_deduction: true,
    rest_deduction_mode: 'period',
    rest_periods: [{ start: '12:00', end: '13:00' }],
    deduct_every_minutes: 300,
    deduct_minutes: 60,
    allow_convert: true,
    convert_mode: 'comp_time',
    comp_time_ratio: 1,
    sync_auto_leave_type: true,
    ...seed,
  }
}

function defaultOvertimePolicy(seed?: Partial<any>) {
  const src = seed || {}
  return {
    workday: defaultOvertimeDayRule(src.workday),
    restday: defaultOvertimeDayRule(src.restday),
    holiday: defaultOvertimeDayRule(src.holiday),
  }
}

function defaultOvertimeDuration(seed?: Partial<any>) {
  return {
    unit: 'hour',
    rounding_mode: 'round',
    decimal_places: 1,
    day_to_hours: 8,
    ...seed,
  }
}

function overtimeMethodLabel(value: string) {
  const map: Record<string, string> = {
    by_approval: '按加班审批时长计算',
    by_clock: '按打卡时长计算',
    by_approval_clock: '按审批和打卡时长计算',
  }
  return map[value] || '按加班审批时长计算'
}

function overtimeRoundingText(mode: string, places: number) {
  const labelMap: Record<string, string> = {
    round: '四舍五入',
    ceil: '向上取整',
    floor: '向下取整',
  }
  return `时长按照「${labelMap[mode] || '四舍五入'}」计算，保留至${places}位小数`
}

function overtimeSummaryText(dayRule: any) {
  if (!dayRule?.enabled) return '不允许加班'
  return overtimeMethodLabel(String(dayRule?.calc_method || 'by_approval'))
}

function inferRuleType(workHourType?: string) {
  if (workHourType === 'comprehensive') return 'shift'
  if (workHourType === 'flexible') return 'free'
  return 'fixed'
}

function workHourTypeFromRuleType(ruleType?: string) {
  if (ruleType === 'shift') return 'comprehensive'
  if (ruleType === 'free') return 'flexible'
  return 'standard'
}

const defaultRuleExtra = () => ({
  employee_type: '固定工',
  assigned_employee_ids: [] as number[],
  rule_type: 'fixed',
  scope_mode: 'all',
  scope_custom_types: [] as string[],
  scope_type: 'all',
  department_ids: [] as number[],
  attendance_group: '',
  attendance_groups: [] as string[],
  label_tags: [] as string[],
  include_sub_departments: true,
  member_ids: [] as number[],
  exclude_employee_ids: [] as number[],
  timezone: 'Asia/Shanghai',
  mobile_remind_on_duty: '前10分钟',
  mobile_remind_off_duty: '准点',
  photo_each_punch: false,
  watermark_photo_required: true,
  face_each_punch: false,
  note_photo_only: false,
  outside_punch_mode: 'abnormal',
  outside_record_sync: false,
  no_punch_employee_ids: [] as number[],
  report_target_ids: [] as number[],
  assist_manager_ids: [] as number[],
  punch_choice_mode: 'mobile',
  shift_mode: 'by_schedule',
  shift_arrange_mode: 'manual',
  shift_no_schedule_employee_select: true,
  shift_no_schedule_system_match: false,
  shift_allow_change_apply: true,
  shift_match_before_minutes: 120,
  shift_match_after_minutes: 0,
  shift_manage_cycle_notes: '',
  shift_templates: [] as Array<{ name: string, clock_in: string, clock_out: string }>,
  time_segments: [] as any[],
  free_workdays: [1, 2, 3, 4, 5] as number[],
  free_work_hours_mode: 'unlimited',
  free_day_start_time: '05:00',
  special_must_dates: [] as Array<{ date: string }>,
  special_no_dates: [] as Array<{ date: string }>,
  legal_holiday_no_punch: true,
  rest_day_no_punch: false,
  rest_day_shift_mode: 'multi',
  rest_day_shift_interval_minutes: 5,
  check_method_mode: 'any',
  enable_patch_apply: false,
  patch_types: [...patchTypeOptions],
  patch_time_limit: '无限制',
  patch_month_limit: '无限制',
  patch_deadline: '不设置',
  patch_reminder: '不提醒',
  enable_approve_punch: true,
  leave_punch_rule: '无需打卡',
  leave_need_punch_before_after: false,
  leave_before_max_advance: '无限制',
  leave_after_max_delay: '无限制',
  leave_retroactive_limit_enabled: true,
  leave_retroactive_limit_months: 1,
  leave_retroactive_cutoff_day: 10,
  leave_retroactive_block_locked_summary: true,
  leave_retroactive_finance_mode: 'approval_month_adjustment',
  early_threshold_minutes: 15,
  enable_flexible: false,
  flex_in_start: '08:00',
  flex_in_end: '09:30',
  lunch_start: '12:00',
  lunch_end: '13:00',
  lunch_as_working: false,
  enable_overtime: true,
  overtime_threshold_minutes: 30,
  weekend_overtime_mode: 'by_minutes',
  overtime_policy: defaultOvertimePolicy(),
  overtime_duration: defaultOvertimeDuration(),
  overtime_rule_workday: '按加班审批时长计算',
  overtime_rule_restday: '按加班审批时长计算',
  overtime_rule_holiday: '按加班审批时长计算',
  overtime_unit: '小时',
  overtime_rounding_rule: '时长按照「四舍五入」计算，保留至1位小数',
  overtime_unit_convert: '1天 = 8小时',
  holiday_plan: '国家标准',
  locations: [] as Array<{ name: string, radius: number, latitude?: number, longitude?: number }>,
  wifi_list: [] as Array<{ name: string, bssid?: string }>,
})
const defaultRuleForm = () => ({
  id: null as number | null,
  name: '',
  work_hour_type: 'standard',
  clock_in_time: '09:00',
  clock_out_time: '18:00',
  flexible_minutes: 0,
  work_hours_per_day: 8,
  overtime_weekday_rate: 1.5,
  overtime_weekend_rate: 2,
  overtime_holiday_rate: 3,
  check_methods: ['GPS'],
  priority: 100,
  effective_date: new Date().toISOString().slice(0, 10),
  is_active: true,
  extra: defaultRuleExtra(),
})
const ruleForm = ref<any>(defaultRuleForm())
const ruleFormRules: FormRules = {
  name: [{ required: true, message: '请输入规则名称', trigger: 'blur' }],
}

function normalizeTime(v?: string | null) {
  if (!v) return ''
  return String(v).slice(0, 5)
}

function normalizeRule(row: any) {
  const extra = { ...defaultRuleExtra(), ...(row.extra_config || {}) }
  extra.rule_type = extra.rule_type || inferRuleType(row.work_hour_type)
  const methods = new Set<string>((extra.check_methods || []).filter((method: string) => method !== '拍照'))
  if (row.require_gps) methods.add('GPS')
  if (row.require_wifi) methods.add('WiFi')
  if ('photo_each_punch' in (row.extra_config || {}) ? extra.photo_each_punch : row.require_photo) methods.add('拍照')
  if (!methods.size) methods.add('GPS')
  extra.scope_mode = extra.scope_mode || (extra.scope_type === 'all' ? 'all' : 'custom')
  extra.assigned_employee_ids = Array.isArray(extra.assigned_employee_ids) ? extra.assigned_employee_ids : []
  extra.scope_custom_types = Array.isArray(extra.scope_custom_types) ? extra.scope_custom_types : []
  if (!extra.scope_custom_types.length) {
    if (extra.scope_type === 'department') extra.scope_custom_types.push('department')
    if (extra.scope_type === 'group') extra.scope_custom_types.push('group')
  }
  extra.include_sub_departments = extra.include_sub_departments !== false
  extra.member_ids = Array.isArray(extra.member_ids) ? extra.member_ids : []
  extra.no_punch_employee_ids = Array.isArray(extra.no_punch_employee_ids) ? extra.no_punch_employee_ids : []
  extra.report_target_ids = Array.isArray(extra.report_target_ids) ? extra.report_target_ids : []
  extra.assist_manager_ids = Array.isArray(extra.assist_manager_ids) ? extra.assist_manager_ids : []
  extra.timezone = String(extra.timezone || 'Asia/Shanghai')
  if (!timezoneOptions.some((x) => x.value === extra.timezone)) extra.timezone = 'Asia/Shanghai'
  extra.mobile_remind_on_duty = mobileRemindOnDutyOptions.includes(String(extra.mobile_remind_on_duty || ''))
    ? String(extra.mobile_remind_on_duty)
    : '前10分钟'
  extra.mobile_remind_off_duty = mobileRemindOffDutyOptions.includes(String(extra.mobile_remind_off_duty || ''))
    ? String(extra.mobile_remind_off_duty)
    : '准点'
  extra.photo_each_punch = !!extra.photo_each_punch
  extra.watermark_photo_required = extra.watermark_photo_required !== false
  extra.face_each_punch = !!extra.face_each_punch
  extra.note_photo_only = !!extra.note_photo_only
  extra.outside_punch_mode = ['abnormal', 'fieldwork', 'forbidden'].includes(String(extra.outside_punch_mode))
    ? String(extra.outside_punch_mode)
    : 'abnormal'
  extra.outside_record_sync = !!extra.outside_record_sync
  extra.attendance_groups = Array.isArray(extra.attendance_groups)
    ? extra.attendance_groups
    : (extra.attendance_group ? [extra.attendance_group] : [])
  extra.label_tags = Array.isArray(extra.label_tags) ? extra.label_tags : []
  extra.rest_day_shift_mode = ['single', 'multi', 'record_only'].includes(String(extra.rest_day_shift_mode))
    ? String(extra.rest_day_shift_mode)
    : 'multi'
  extra.rest_day_shift_interval_minutes = Number(extra.rest_day_shift_interval_minutes || 0)
  extra.free_day_start_time = normalizeTime(extra.free_day_start_time || '') || '05:00'
  extra.free_work_hours_mode = String(extra.free_work_hours_mode || 'unlimited')
  extra.shift_arrange_mode = extra.shift_arrange_mode === 'optional' ? 'optional' : 'manual'
  extra.shift_no_schedule_employee_select = extra.shift_no_schedule_employee_select !== false
  extra.shift_no_schedule_system_match = !!extra.shift_no_schedule_system_match
  extra.shift_allow_change_apply = extra.shift_allow_change_apply !== false
  extra.shift_match_before_minutes = Number(extra.shift_match_before_minutes ?? 120)
  extra.shift_match_after_minutes = Number(extra.shift_match_after_minutes ?? 0)
  extra.leave_need_punch_before_after = !!extra.leave_need_punch_before_after || extra.leave_punch_rule === '离岗返岗打卡'
  if (!leavePunchTimeLimitOptions.includes(String(extra.leave_before_max_advance || ''))) extra.leave_before_max_advance = '无限制'
  if (!leavePunchTimeLimitOptions.includes(String(extra.leave_after_max_delay || ''))) extra.leave_after_max_delay = '无限制'
  extra.enable_patch_apply = !!extra.enable_patch_apply
  extra.patch_types = Array.isArray(extra.patch_types)
    ? extra.patch_types.map((item: any) => String(item)).filter((item: string) => patchTypeOptions.includes(item))
    : []
  if (!extra.patch_types.length) extra.patch_types = [...patchTypeOptions]
  if (!patchTimeLimitOptions.includes(String(extra.patch_time_limit || ''))) extra.patch_time_limit = '无限制'
  if (!patchMonthLimitOptions.includes(String(extra.patch_month_limit || ''))) extra.patch_month_limit = '无限制'
  if (!patchDeadlineOptions.includes(String(extra.patch_deadline || ''))) extra.patch_deadline = '不设置'
  extra.overtime_policy = defaultOvertimePolicy(extra.overtime_policy)
  extra.overtime_duration = defaultOvertimeDuration(extra.overtime_duration)
  syncOvertimeLegacySummary(extra)
  extra.time_segments = Array.isArray(extra.time_segments) && extra.time_segments.length
    ? extra.time_segments.map((s: any, idx: number) => defaultTimeSegment({
      ...s,
      name: s?.name || buildSegmentName(idx),
      weekdays: Array.isArray(s.weekdays) && s.weekdays.length ? s.weekdays : [1, 2, 3, 4, 5],
      biweekly_enabled: !!s.biweekly_enabled,
      this_week_weekdays: Array.isArray(s.this_week_weekdays) && s.this_week_weekdays.length
        ? s.this_week_weekdays
        : (Array.isArray(s.weekdays) && s.weekdays.length ? s.weekdays : [1, 2, 3, 4, 5]),
      next_week_weekdays: Array.isArray(s.next_week_weekdays) && s.next_week_weekdays.length
        ? s.next_week_weekdays
        : (Array.isArray(s.weekdays) && s.weekdays.length ? s.weekdays : [1, 2, 3, 4, 5]),
      half_day_am: Array.isArray(s.half_day_am) ? s.half_day_am : ['09:00', '13:00'],
      half_day_pm: Array.isArray(s.half_day_pm) ? s.half_day_pm : ['13:00', '17:00'],
    }))
    : [defaultTimeSegment({
      name: '工作时段1',
      clock_in: normalizeTime(row.clock_in_time) || '09:00',
      clock_out: normalizeTime(row.clock_out_time) || '17:00',
    })]
  const freeSegment = extra.time_segments[0] || {}
  extra.free_workdays = Array.isArray(extra.free_workdays) && extra.free_workdays.length
    ? extra.free_workdays
    : (Array.isArray(freeSegment.weekdays) && freeSegment.weekdays.length ? freeSegment.weekdays : [1, 2, 3, 4, 5])
  if (!extra.free_work_hours_mode.startsWith('limit:') && extra.free_work_hours_mode !== 'unlimited') {
    const maybeHours = Number(row.work_hours_per_day || 0)
    extra.free_work_hours_mode = maybeHours > 0 ? `limit:${Math.round(maybeHours)}` : 'unlimited'
  }
  extra.shift_mode = extra.shift_mode || 'by_schedule'
  extra.shift_templates = Array.isArray(extra.shift_templates) ? extra.shift_templates.map((x: any) => defaultShiftTemplate(x)) : []
  if (extra.rule_type === 'shift' && !extra.shift_templates.length) {
    extra.shift_templates = defaultShiftTemplatePreset()
  }
  extra.special_must_dates = Array.isArray(extra.special_must_dates)
    ? extra.special_must_dates.map((x: any) => defaultSpecialDateConfig(
      x?.mode ? x : { mode: 'single', date: x?.date || x || '', repeat: 'none' }
    ))
    : []
  extra.special_no_dates = Array.isArray(extra.special_no_dates)
    ? extra.special_no_dates.map((x: any) => defaultSpecialDateConfig(
      x?.mode ? x : { mode: 'single', date: x?.date || x || '', repeat: 'none' }
    ))
    : []
  extra.locations = Array.isArray(extra.locations)
    ? extra.locations.map((loc: any) => {
      const lat = Number(loc?.latitude)
      const lng = Number(loc?.longitude)
      return {
        name: loc?.name || '',
        radius: Number(loc?.radius || 300),
        latitude: Number.isFinite(lat) ? lat : undefined,
        longitude: Number.isFinite(lng) ? lng : undefined,
      }
    })
    : []
  if (
    !extra.locations.length
    && row.gps_latitude !== null
    && row.gps_latitude !== undefined
    && row.gps_longitude !== null
    && row.gps_longitude !== undefined
  ) {
    extra.locations.push({
      name: row.name ? `${row.name}-GPS中心点` : 'GPS中心点',
      radius: Number(row.gps_radius_meters || 300),
      latitude: Number(row.gps_latitude),
      longitude: Number(row.gps_longitude),
    })
  }
  extra.wifi_list = Array.isArray(extra.wifi_list) ? extra.wifi_list : []

  if (extra.scope_type === 'department' && (!extra.department_ids || !extra.department_ids.length) && row.department_id) {
    extra.department_ids = [row.department_id]
  }
  if (!extra.scope_type) extra.scope_type = row.department_id ? 'department' : 'all'
  if (extra.scope_mode === 'custom' && !extra.scope_custom_types.length && extra.member_ids.length) {
    extra.scope_custom_types = ['employee']
  }
  return {
    ...defaultRuleForm(),
    ...row,
    work_hour_type: workHourTypeFromRuleType(extra.rule_type),
    clock_in_time: normalizeTime(row.clock_in_time) || extra.time_segments?.[0]?.clock_in || '',
    clock_out_time: normalizeTime(row.clock_out_time) || extra.time_segments?.[0]?.clock_out || '',
    check_methods: Array.from(methods),
    extra,
  }
}

function formatRuleType(rule: any) {
  const type = (rule.extra?.rule_type || rule.extra_config?.rule_type || inferRuleType(rule.work_hour_type)) as string
  return ruleTypeOptions.find((x) => x.value === type)?.label || '固定时间上下班'
}

function formatRuleWorkTime(rule: any) {
  const extra = rule.extra || rule.extra_config || {}
  const first = Array.isArray(extra.time_segments) && extra.time_segments.length ? extra.time_segments[0] : null
  if (first?.clock_in && first?.clock_out) return `${first.clock_in}-${first.clock_out}`
  if (rule.clock_in_time && rule.clock_out_time) return `${normalizeTime(rule.clock_in_time)}-${normalizeTime(rule.clock_out_time)}`
  return '--:--'
}

function formatRuleScope(rule: any) {
  const extra = rule.extra || rule.extra_config || {}
  if (extra.scope_mode === 'project') return '按项目参与人员与日期'
  const assignedIds: number[] = Array.isArray(extra.assigned_employee_ids)
    ? extra.assigned_employee_ids.map((id: any) => Number(id)).filter((id: number) => Number.isFinite(id))
    : []
  if (assignedIds.length) {
    const memberNames = assignedIds.map((id: number) => employeeMap.value[id]?.name || `员工#${id}`)
    return `打卡人员：${memberNames.join('、')}`
  }
  if ((extra.scope_mode || 'all') === 'all') return '全部员工'
  const parts: string[] = []
  if (extra.scope_custom_types?.includes?.('department')) {
    const ids: number[] = extra.department_ids || (rule.department_id ? [rule.department_id] : [])
    if (ids.length) {
      const names = ids.map((id) => flatDeptOptions.value.find((d: any) => d.id === id)?.name || `部门#${id}`)
      parts.push(`部门：${names.join('、')}${extra.include_sub_departments ? '(含子部门)' : ''}`)
    }
  }
  if (extra.scope_custom_types?.includes?.('employee')) {
    const memberIds = Array.from(new Set([...(extra.member_ids || []), ...assignedIds]))
    const memberNames = memberIds.map((id: number) => employeeMap.value[id]?.name || `员工#${id}`)
    if (memberNames.length) parts.push(`成员：${memberNames.join('、')}`)
  }
  if (extra.scope_custom_types?.includes?.('group')) {
    const groups = extra.attendance_groups || (extra.attendance_group ? [extra.attendance_group] : [])
    if (groups.length) parts.push(`考勤组：${groups.join('、')}`)
  }
  if (extra.scope_custom_types?.includes?.('tag')) {
    const tags = extra.label_tags || []
    if (tags.length) parts.push(`标签：${tags.join('、')}`)
  }
  return parts.length ? parts.join(' / ') : '自定义范围'
}

function ruleScopeKey(rule: any) {
  return rule?.id ?? rule?.name ?? ''
}

function isRuleScopeExpanded(rule: any) {
  return expandedRuleScopeIds.value.has(ruleScopeKey(rule))
}

function ruleScopeNeedsToggle(rule: any) {
  return formatRuleScope(rule).length > 18
}

function toggleRuleScope(rule: any) {
  const key = ruleScopeKey(rule)
  const next = new Set(expandedRuleScopeIds.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedRuleScopeIds.value = next
}

function changeRuleType(ruleType: string) {
  const extra = { ...defaultRuleExtra(), ...(ruleForm.value.extra || {}) }
  extra.rule_type = ruleType
  if (!Array.isArray(extra.time_segments)) extra.time_segments = []
  if (!Array.isArray(extra.shift_templates)) extra.shift_templates = []
  if (ruleType === 'free') {
    if (!Array.isArray(extra.free_workdays) || !extra.free_workdays.length) {
      extra.free_workdays = [1, 2, 3, 4, 5]
    }
    extra.free_day_start_time = normalizeTime(extra.free_day_start_time) || '05:00'
    if (!String(extra.free_work_hours_mode || '').startsWith('limit:') && extra.free_work_hours_mode !== 'unlimited') {
      extra.free_work_hours_mode = 'unlimited'
    }
    if (!extra.time_segments.length) {
      extra.time_segments = [defaultTimeSegment({ name: '工作时段1' })]
    }
    extra.time_segments = extra.time_segments.map((segment: any) => ({
      ...segment,
      weekdays: [...extra.free_workdays],
      check_in_required: false,
      check_out_required: false,
      punch_start: extra.free_day_start_time,
      punch_end: minutesToHm(hmToMinutes(extra.free_day_start_time) - 1),
    }))
  }
  if (ruleType === 'shift' && !extra.shift_templates.length) {
    extra.shift_templates = defaultShiftTemplatePreset()
  }
  ruleForm.value.work_hour_type = workHourTypeFromRuleType(ruleType)
  ruleForm.value.extra = extra
}

function syncAssigneeTreeCheckedKeys() {
  const checkedKeys = normalizeSelectableAssigneeIds(ruleForm.value.extra.assigned_employee_ids || [])
    .map((id: number) => assigneeEmpNodeKey(id))
  nextTick(() => {
    assigneeTreeRef.value?.setCheckedKeys?.(checkedKeys)
  })
}

function handleAssigneeTreeCheck() {
  const checkedKeys: string[] = assigneeTreeRef.value?.getCheckedKeys?.(false) || []
  const employeeIds = checkedKeys
    .filter((key) => String(key).startsWith('emp-'))
    .map((key) => Number(String(key).slice(4)))
    .filter((id) => Number.isFinite(id))
  ruleForm.value.extra.assigned_employee_ids = Array.from(new Set(employeeIds))
}

function filterAssigneeTreeNode(keyword: string, data: AssigneeTreeNode) {
  if (!keyword) return true
  const text = keyword.trim().toLowerCase()
  if (!text) return true
  return String(data.searchText || `${data.label || ''} ${data.employeeNo || ''}`).toLowerCase().includes(text)
}

function selectAllAssignees() {
  ruleForm.value.extra.assigned_employee_ids = normalizeSelectableAssigneeIds((employeeOptions.value || []).map((employee: any) => employee.id))
  syncAssigneeTreeCheckedKeys()
}

function clearAssignees() {
  ruleForm.value.extra.assigned_employee_ids = []
  syncAssigneeTreeCheckedKeys()
}

function removeAssignee(employeeId: number) {
  ruleForm.value.extra.assigned_employee_ids = normalizeSelectableAssigneeIds(ruleForm.value.extra.assigned_employee_ids || [])
    .filter((id: number) => id !== employeeId)
  if (assigneePickerVisible.value) {
    syncAssigneeTreeCheckedKeys()
  }
}

function memberPickerTreeRef(kind: MemberPickerKind) {
  if (kind === 'no_punch') return noPunchTreeRef.value
  if (kind === 'report_target') return reportTargetTreeRef.value
  return assistManagerTreeRef.value
}

function syncMemberPickerTreeCheckedKeys(kind: MemberPickerKind) {
  const checkedKeys = normalizedMemberIds(kind).map((id) => assigneeEmpNodeKey(Number(id)))
  nextTick(() => {
    memberPickerTreeRef(kind)?.setCheckedKeys?.(checkedKeys)
  })
}

function handleMemberPickerTreeCheck(kind: MemberPickerKind) {
  const checkedKeys: string[] = memberPickerTreeRef(kind)?.getCheckedKeys?.(false) || []
  const employeeIds = checkedKeys
    .filter((key) => String(key).startsWith('emp-'))
    .map((key) => Number(String(key).slice(4)))
    .filter((id) => Number.isFinite(id))
  setMemberPickerIds(kind, employeeIds)
}

function selectAllMemberPicker(kind: MemberPickerKind) {
  const ids = (employeeOptions.value || [])
    .map((employee: any) => Number(employee.id))
    .filter((id: number) => Number.isFinite(id))
  setMemberPickerIds(kind, ids)
  syncMemberPickerTreeCheckedKeys(kind)
}

function clearMemberPicker(kind: MemberPickerKind) {
  setMemberPickerIds(kind, [])
  syncMemberPickerTreeCheckedKeys(kind)
}

function removeMemberPickerEmployee(kind: MemberPickerKind, employeeId: number) {
  setMemberPickerIds(
    kind,
    normalizedMemberIds(kind).filter((id) => id !== Number(employeeId))
  )
  syncMemberPickerTreeCheckedKeys(kind)
}

const restdaySummaryText = computed(() => {
  const rawMode = String(ruleForm.value.extra?.rest_day_shift_mode || 'multi')
  const mode = rawMode === 'single'
    ? '交替打卡1次'
    : rawMode === 'record_only'
      ? '仅记录打卡时间和位置'
      : '交替打卡多次'
  const minutes = Number(ruleForm.value.extra?.rest_day_shift_interval_minutes || 0)
  if (rawMode === 'record_only') return `打卡交替方式：${mode}`
  return `打卡交替方式：${mode}；打卡间隔时间：${minutes}分钟`
})

const leavePunchSummaryText = computed(() => {
  const enabled = !!ruleForm.value.extra?.leave_need_punch_before_after
  if (!enabled) return '请假时离岗返岗「无需打卡」'
  const before = String(ruleForm.value.extra?.leave_before_max_advance || '无限制')
  const after = String(ruleForm.value.extra?.leave_after_max_delay || '无限制')
  return `请假时离岗返岗「需打卡」；离岗最多提前${before}，返岗最多延迟${after}`
})

const shiftArrangeModeText = computed(() => (
  ruleForm.value.extra?.shift_arrange_mode === 'optional' ? '无需事先排班（成员自选班次）' : '手动排班（可选无需事先排班）'
))

const shiftTemplateSummaryText = computed(() => {
  const list = Array.isArray(ruleForm.value.extra?.shift_templates) ? ruleForm.value.extra.shift_templates : []
  if (!list.length) return '未配置'
  const fmtOut = (cin: string, cout: string) => (hmToMinutes(cout || '00:00') < hmToMinutes(cin || '00:00') ? `次日${cout}` : cout)
  return list
    .filter((x: any) => x.name !== '休息')
    .map((x: any) => `${x.name}(${x.clock_in}-${fmtOut(x.clock_in, x.clock_out)})`)
    .join('、') || '仅休息班'
})

const shiftSettingMoreText = computed(() => {
  const ex = ruleForm.value.extra || {}
  const arr: string[] = []
  if (ex.shift_no_schedule_employee_select) arr.push('未排班时成员自选班次')
  if (ex.shift_no_schedule_system_match) arr.push('未排班时系统自动对班')
  if (ex.shift_allow_change_apply) arr.push('成员可发起调班申请')
  return arr.join('，') || '未设置'
})

const shiftArrangeDays = computed(() => {
  const [y, m] = String(shiftArrangeMonth.value || '').split('-').map((x) => Number(x))
  if (!y || !m) return []
  const lastDay = new Date(y, m, 0).getDate()
  const out: Array<{ key: string, day: number, weekText: string }> = []
  const map = ['日', '一', '二', '三', '四', '五', '六']
  for (let day = 1; day <= lastDay; day += 1) {
    const d = new Date(y, m - 1, day)
    const key = `${y}-${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    out.push({ key, day, weekText: map[d.getDay()] })
  }
  return out
})

const shiftArrangeEmployees = computed(() => {
  const selectedIds = Array.isArray(ruleForm.value.extra?.assigned_employee_ids) ? ruleForm.value.extra.assigned_employee_ids : []
  const keyword = String(shiftArrangeKeyword.value || '').trim()
  const base = (employeeOptions.value || []).filter((e: any) => selectedIds.includes(e.id))
  if (!keyword) return base
  return base.filter((e: any) => `${e.name || ''}${e.employee_no || ''}`.includes(keyword))
})

function openRestdaySettingDialog() {
  restdaySettingDraft.value = {
    shift_mode: ['single', 'multi', 'record_only'].includes(String(ruleForm.value.extra?.rest_day_shift_mode))
      ? String(ruleForm.value.extra?.rest_day_shift_mode)
      : 'multi',
    interval_minutes: Number(ruleForm.value.extra?.rest_day_shift_interval_minutes || 0),
  }
  restdaySettingDialogVisible.value = true
}

function saveRestdaySetting() {
  ruleForm.value.extra.rest_day_shift_mode = ['single', 'multi', 'record_only'].includes(String(restdaySettingDraft.value.shift_mode))
    ? String(restdaySettingDraft.value.shift_mode)
    : 'multi'
  ruleForm.value.extra.rest_day_shift_interval_minutes = Number(restdaySettingDraft.value.interval_minutes || 0)
  restdaySettingDialogVisible.value = false
}

function openLeavePunchLearnMore() {
  ElMessage.info('请假按半天/小时审批通过后，可按该规则进行离岗返岗打卡校验')
}

function openLeavePunchSettingDialog() {
  leavePunchSettingDraft.value = {
    need_punch: !!ruleForm.value.extra?.leave_need_punch_before_after,
    before_max_advance: String(ruleForm.value.extra?.leave_before_max_advance || '无限制'),
    after_max_delay: String(ruleForm.value.extra?.leave_after_max_delay || '无限制'),
  }
  leavePunchSettingSnapshot.value = JSON.stringify(leavePunchSettingDraft.value)
  leavePunchSettingDialogVisible.value = true
}

function saveLeavePunchSetting() {
  const next = {
    need_punch: !!leavePunchSettingDraft.value.need_punch,
    before_max_advance: leavePunchTimeLimitOptions.includes(String(leavePunchSettingDraft.value.before_max_advance))
      ? String(leavePunchSettingDraft.value.before_max_advance)
      : '无限制',
    after_max_delay: leavePunchTimeLimitOptions.includes(String(leavePunchSettingDraft.value.after_max_delay))
      ? String(leavePunchSettingDraft.value.after_max_delay)
      : '无限制',
  }
  ruleForm.value.extra.leave_need_punch_before_after = next.need_punch
  ruleForm.value.extra.leave_punch_rule = next.need_punch ? '离岗返岗打卡' : '无需打卡'
  ruleForm.value.extra.leave_before_max_advance = next.before_max_advance
  ruleForm.value.extra.leave_after_max_delay = next.after_max_delay
  leavePunchSettingSnapshot.value = JSON.stringify(next)
  leavePunchSettingDialogVisible.value = false
}

async function handleLeavePunchNavigateSetting() {
  const current = JSON.stringify({
    need_punch: !!leavePunchSettingDraft.value.need_punch,
    before_max_advance: String(leavePunchSettingDraft.value.before_max_advance || '无限制'),
    after_max_delay: String(leavePunchSettingDraft.value.after_max_delay || '无限制'),
  })
  if (current !== leavePunchSettingSnapshot.value) {
    try {
      await ElMessageBox.confirm(
        '即将前往上下班时间，是否保存请假打卡设置？',
        '提示',
        {
          confirmButtonText: '保存',
          cancelButtonText: '不保存',
          type: 'info',
          distinguishCancelAndClose: true,
        }
      )
      saveLeavePunchSetting()
    } catch (error: any) {
      if (error === 'close') return
    }
  }
  ElMessage.info('可在「上下班时间-半天工作时间」继续配置请假打卡规则')
}

const activeOvertimeDayRule = computed<any>(() => {
  const tab = overtimeRuleActiveTab.value
  const draft = overtimeRuleDraft.value || defaultOvertimePolicy()
  if (!draft[tab]) draft[tab] = defaultOvertimeDayRule()
  const current = draft[tab]
  if (!Array.isArray(current.rest_periods) || !current.rest_periods.length) {
    current.rest_periods = [{ start: '12:00', end: '13:00' }]
  }
  return current
})

function addOvertimeRestPeriod() {
  const current = activeOvertimeDayRule.value
  if (!Array.isArray(current.rest_periods)) current.rest_periods = []
  current.rest_periods.push({ start: '', end: '' })
}

function removeOvertimeRestPeriod(index: number) {
  const current = activeOvertimeDayRule.value
  if (!Array.isArray(current.rest_periods)) return
  current.rest_periods.splice(index, 1)
  if (!current.rest_periods.length) {
    current.rest_periods.push({ start: '', end: '' })
  }
}

function syncOvertimeLegacySummary(extra: any) {
  const policy = defaultOvertimePolicy(extra?.overtime_policy)
  const duration = defaultOvertimeDuration(extra?.overtime_duration)
  extra.overtime_policy = policy
  extra.overtime_duration = duration
  extra.overtime_rule_workday = overtimeSummaryText(policy.workday)
  extra.overtime_rule_restday = overtimeSummaryText(policy.restday)
  extra.overtime_rule_holiday = overtimeSummaryText(policy.holiday)
  extra.overtime_unit = duration.unit === 'day' ? '天' : '小时'
  extra.overtime_rounding_rule = overtimeRoundingText(
    String(duration.rounding_mode || 'round'),
    Number(duration.decimal_places ?? 1)
  )
  extra.overtime_unit_convert = `1天 = ${Number(duration.day_to_hours || 8)}小时`
}

function openOvertimeRuleDialog() {
  overtimeRuleDraft.value = defaultOvertimePolicy(ruleForm.value.extra?.overtime_policy)
  overtimeRuleActiveTab.value = 'workday'
  overtimeRuleDialogVisible.value = true
}

function validateOvertimePolicyDraft(policyDraft: any) {
  const policy = defaultOvertimePolicy(policyDraft)
  for (const dayKey of ['workday', 'restday', 'holiday'] as const) {
    const dayRule = policy[dayKey]
    if (!dayRule?.enabled || dayRule.rest_deduction_mode !== 'period' || !dayRule.allow_rest_deduction) continue
    const cleaned = Array.isArray(dayRule.rest_periods)
      ? dayRule.rest_periods.filter((item: any) => item?.start || item?.end)
      : []
    for (const item of cleaned) {
      if (!item.start || !item.end) {
        ElMessage.warning('休息时段请完整填写开始和结束')
        return null
      }
    }
    dayRule.rest_periods = cleaned.length ? cleaned : [{ start: '12:00', end: '13:00' }]
  }
  return policy
}

function saveOvertimeRuleDialog() {
  if (!ruleForm.value.extra) ruleForm.value.extra = defaultRuleExtra()
  const validatedPolicy = validateOvertimePolicyDraft(overtimeRuleDraft.value)
  if (!validatedPolicy) return
  ruleForm.value.extra.overtime_policy = validatedPolicy
  syncOvertimeLegacySummary(ruleForm.value.extra)
  overtimeRuleDialogVisible.value = false
}

function openOvertimeDurationDialog() {
  overtimeDurationDraft.value = defaultOvertimeDuration(ruleForm.value.extra?.overtime_duration)
  overtimeDurationDialogVisible.value = true
}

function saveOvertimeDurationDialog() {
  if (!ruleForm.value.extra) ruleForm.value.extra = defaultRuleExtra()
  ruleForm.value.extra.overtime_duration = defaultOvertimeDuration(overtimeDurationDraft.value)
  syncOvertimeLegacySummary(ruleForm.value.extra)
  overtimeDurationDialogVisible.value = false
}

function formatWeekdays(days: number[] = []) {
  if (!days.length) return '-'
  const map: Record<number, string> = { 1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', 7: '周日' }
  const sorted = Array.from(new Set(days)).sort((a, b) => a - b)
  const key = sorted.join(',')
  if (key === '1,2,3,4,5') return '周一至周五'
  if (key === '1,2,3,4,5,6') return '周一至周六'
  if (key === '1,2,3,4,5,6,7') return '周一至周日'
  if (key === '6,7') return '周六、周日'
  return sorted.map((d) => map[d] || '').filter(Boolean).join('、')
}

function formatSegmentWeekdays(segment: any) {
  if (segment?.biweekly_enabled) {
    const thisWeek = formatWeekdays(segment?.this_week_weekdays || [])
    const nextWeek = formatWeekdays(segment?.next_week_weekdays || [])
    return `本周(${thisWeek}) / 下周(${nextWeek})`
  }
  return formatWeekdays(segment?.weekdays || [])
}

function enableBiweeklyForDraft() {
  segmentDraft.value.biweekly_enabled = true
  const fallback = Array.isArray(segmentDraft.value.weekdays) && segmentDraft.value.weekdays.length
    ? segmentDraft.value.weekdays
    : [1, 2, 3, 4, 5]
  segmentDraft.value.this_week_weekdays = Array.isArray(segmentDraft.value.this_week_weekdays) && segmentDraft.value.this_week_weekdays.length
    ? segmentDraft.value.this_week_weekdays
    : [...fallback]
  segmentDraft.value.next_week_weekdays = Array.isArray(segmentDraft.value.next_week_weekdays) && segmentDraft.value.next_week_weekdays.length
    ? segmentDraft.value.next_week_weekdays
    : [...fallback]
}

function disableBiweeklyForDraft() {
  segmentDraft.value.biweekly_enabled = false
  const merged = Array.from(new Set([...(segmentDraft.value.this_week_weekdays || []), ...(segmentDraft.value.next_week_weekdays || [])]))
  segmentDraft.value.weekdays = merged.length ? merged : [1, 2, 3, 4, 5]
}

function mondayStart(date: Date) {
  const d = new Date(date)
  const day = d.getDay()
  const diff = day === 0 ? -6 : 1 - day
  d.setDate(d.getDate() + diff)
  d.setHours(0, 0, 0, 0)
  return d
}

const workCalendarCells = computed(() => {
  const month = workCalendarPreviewMonth.value || new Date().toISOString().slice(0, 7)
  const [year, mon] = month.split('-').map(Number)
  const firstDay = new Date(year, (mon || 1) - 1, 1)
  const monthStartMonday = mondayStart(firstDay)
  const nextMonthFirstDay = new Date(year, mon || 1, 1)
  const lastDay = new Date(nextMonthFirstDay.getTime() - 24 * 3600 * 1000)
  const monthEndSunday = new Date(mondayStart(lastDay).getTime() + 6 * 24 * 3600 * 1000)
  const todayMonday = mondayStart(new Date())
  const cells: Array<{ key: string, day: number, inMonth: boolean, isWorkday: boolean }> = []
  for (let t = monthStartMonday.getTime(); t <= monthEndSunday.getTime(); t += 24 * 3600 * 1000) {
    const d = new Date(t)
    const weekday = d.getDay() === 0 ? 7 : d.getDay()
    const inMonth = d.getMonth() === (mon || 1) - 1
    let isWorkday = false
    if (segmentDraft.value.biweekly_enabled) {
      const weekOffset = Math.floor((mondayStart(d).getTime() - todayMonday.getTime()) / (7 * 24 * 3600 * 1000))
      const isThisPattern = Math.abs(weekOffset % 2) === 0
      const setDays = isThisPattern ? (segmentDraft.value.this_week_weekdays || []) : (segmentDraft.value.next_week_weekdays || [])
      isWorkday = setDays.includes(weekday)
    } else {
      isWorkday = (segmentDraft.value.weekdays || []).includes(weekday)
    }
    cells.push({
      key: `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`,
      day: d.getDate(),
      inMonth,
      isWorkday,
    })
  }
  return cells
})

const holidayYearOptions = computed(() => {
  const current = new Date().getFullYear()
  return [current - 1, current, current + 1, current + 2]
})

const holidayCalendarCells = computed(() => {
  const year = holidayCalendarYear.value
  const month = holidayCalendarMonth.value
  const firstDay = new Date(year, month - 1, 1)
  const monthStartMonday = mondayStart(firstDay)
  const nextMonthFirstDay = new Date(year, month, 1)
  const lastDay = new Date(nextMonthFirstDay.getTime() - 24 * 3600 * 1000)
  const monthEndSunday = new Date(mondayStart(lastDay).getTime() + 6 * 24 * 3600 * 1000)
  const today = new Date()
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  const preset = holidayPresetMap[year] || { workdays: [], restdays: [], tags: {} as Record<string, string> }
  const workdaySet = new Set(preset.workdays)
  const restdaySet = new Set(preset.restdays)
  const serverMap = holidayCalendarServerMap.value || {}
  const cells: Array<{
    key: string
    day: number
    inMonth: boolean
    isToday: boolean
    dayType: 'work' | 'rest'
    tag: string
  }> = []
  for (let t = monthStartMonday.getTime(); t <= monthEndSunday.getTime(); t += 24 * 3600 * 1000) {
    const d = new Date(t)
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const weekday = d.getDay()
    const isWeekend = weekday === 0 || weekday === 6
    let dayType: 'work' | 'rest' = isWeekend ? 'rest' : 'work'
    if (workdaySet.has(key)) dayType = 'work'
    if (restdaySet.has(key)) dayType = 'rest'
    const server = serverMap[key]
    if (server) dayType = server.dayType
    cells.push({
      key,
      day: d.getDate(),
      inMonth: d.getMonth() === month - 1,
      isToday: key === todayKey,
      dayType,
      tag: server?.tag || preset.tags[key] || '',
    })
  }
  return cells
})

async function loadHolidayCalendarFromServer(year: number) {
  holidayCalendarLoading.value = true
  try {
    const rows = await get('/attendance/calendar', {
      start_date: `${year}-01-01`,
      end_date: `${year}-12-31`,
    }).catch(() => [])
    const list = Array.isArray(rows) ? rows : (rows?.items || rows?.data || [])
    const mapped: Record<string, { dayType: 'work' | 'rest', tag: string }> = {}
    for (const row of list) {
      const key = String(row?.date || '').slice(0, 10)
      if (!key) continue
      const type = String(row?.day_type || '')
      if (type === 'holiday') {
        mapped[key] = { dayType: 'rest', tag: normalizeHolidayCalendarTag(row?.holiday_name) || '法定节假日' }
      } else if (type === 'special_workday') {
        mapped[key] = { dayType: 'work', tag: normalizeHolidayCalendarTag(row?.holiday_name) || '补班' }
      } else if (type === 'weekend') {
        mapped[key] = { dayType: 'rest', tag: normalizeHolidayCalendarTag(row?.holiday_name) || '' }
      } else if (type === 'workday') {
        mapped[key] = { dayType: 'work', tag: normalizeHolidayCalendarTag(row?.holiday_name) || '' }
      }
    }
    holidayCalendarServerMap.value = mapped
  } finally {
    holidayCalendarLoading.value = false
  }
}

async function openHolidayCalendar() {
  const now = new Date()
  holidayCalendarYear.value = now.getFullYear()
  holidayCalendarMonth.value = now.getMonth() + 1
  holidayCalendarVisible.value = true
  await loadHolidayCalendarFromServer(holidayCalendarYear.value)
}

function goToCurrentHolidayMonth() {
  const now = new Date()
  holidayCalendarYear.value = now.getFullYear()
  holidayCalendarMonth.value = now.getMonth() + 1
}

function switchHolidayMonth(step: number) {
  const next = new Date(holidayCalendarYear.value, holidayCalendarMonth.value - 1 + step, 1)
  holidayCalendarYear.value = next.getFullYear()
  holidayCalendarMonth.value = next.getMonth() + 1
}

watch(holidayCalendarYear, (year) => {
  if (!holidayCalendarVisible.value) return
  loadHolidayCalendarFromServer(year)
})

watch(shiftArrangeMonth, () => {
  if (!shiftArrangeDialogVisible.value) return
  loadShiftAssignments()
})

watch(assigneeKeyword, (keyword) => {
  assigneeTreeRef.value?.filter?.(keyword || '')
})

watch(assigneePickerVisible, (visible) => {
  if (!visible) return
  assigneeKeyword.value = ''
  syncAssigneeTreeCheckedKeys()
})

watch(noPunchKeyword, (keyword) => {
  noPunchTreeRef.value?.filter?.(keyword || '')
})

watch(reportTargetKeyword, (keyword) => {
  reportTargetTreeRef.value?.filter?.(keyword || '')
})

watch(assistManagerKeyword, (keyword) => {
  assistManagerTreeRef.value?.filter?.(keyword || '')
})

watch(noPunchSelectorVisible, (visible) => {
  if (!visible) return
  noPunchKeyword.value = ''
  syncMemberPickerTreeCheckedKeys('no_punch')
})

watch(reportTargetSelectorVisible, (visible) => {
  if (!visible) return
  reportTargetKeyword.value = ''
  syncMemberPickerTreeCheckedKeys('report_target')
})

watch(assistManagerSelectorVisible, (visible) => {
  if (!visible) return
  assistManagerKeyword.value = ''
  syncMemberPickerTreeCheckedKeys('assist_manager')
})

function flexModeLabel(mode: string) {
  if (mode === 'late_leave') return '允许晚到早走'
  if (mode === 'both') return '允许早到早走、晚到晚走'
  return '不允许'
}

function formatPunchWindow(start?: string, end?: string) {
  if (!start || !end) return '--:-- - --:--'
  return hmToMinutes(end) < hmToMinutes(start) ? `${start} - 次日${end}` : `${start} - ${end}`
}

function minutesToHm(total: number) {
  let m = total % (24 * 60)
  if (m < 0) m += 24 * 60
  const h = Math.floor(m / 60)
  const mm = m % 60
  return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}

function formatHmForHint(base: string, target: string) {
  const b = hmToMinutes(base || '00:00')
  const t = hmToMinutes(target || '00:00')
  return t < b ? `次日${target}` : target
}

function isNextDayRange(start?: string, end?: string) {
  if (!start || !end) return false
  return hmToMinutes(end) < hmToMinutes(start)
}

function segmentWorkPeriods(segment: any) {
  const periods = [{
    clock_in: segment.clock_in || '09:00',
    clock_out: segment.clock_out || '17:00',
    check_in_required: segment.check_in_required !== false,
    check_out_required: segment.check_out_required !== false,
  }]
  for (const p of (segment.extra_periods || [])) {
    periods.push({
      clock_in: p.clock_in || '09:00',
      clock_out: p.clock_out || '17:00',
      check_in_required: p.check_in_required !== false,
      check_out_required: p.check_out_required !== false,
    })
  }
  return periods
}

function rebuildPunchWindowsFromSegment(segment: any) {
  const periods = segmentWorkPeriods(segment)
  const existing = Array.isArray(segment.punch_windows) ? segment.punch_windows : []
  const rows: any[] = periods.map((p: any, i: number) => {
    const prev = periods[i - 1]
    const ex = existing[i] || {}
    return {
      title: `工作时段${i + 1}`,
      clock_in: p.clock_in,
      clock_out: p.clock_out,
      in_start: ex.in_start || (i === 0 ? (segment.punch_start || '04:00') : minutesToHm(hmToMinutes(prev.clock_out || '00:00') + 30)),
      in_end: ex.in_end || minutesToHm(hmToMinutes(p.clock_in || '00:00') - 1),
      out_start: ex.out_start || minutesToHm(hmToMinutes(p.clock_in || '00:00') + 1),
      out_end: ex.out_end || '',
      removable: i > 0,
    }
  })

  for (let i = 0; i < rows.length; i++) {
    if (!rows[i].out_end) {
      const next = rows[i + 1]
      rows[i].out_end = next ? minutesToHm(hmToMinutes(next.in_start || '00:00') - 1) : (segment.punch_end || '03:59')
    }
  }
  return rows
}

function getInPunchHint(row: any) {
  const normalStart = row?.in_start || '--:--'
  const normalEnd = row?.clock_in || '--:--'
  const lateStart = row?.clock_in ? minutesToHm(hmToMinutes(row.clock_in) + 1) : '--:--'
  const lateEnd = row?.in_end || '--:--'
  return `${normalStart}-${formatHmForHint(normalStart, normalEnd)}打卡为正常，${formatHmForHint(normalStart, lateStart)}-${formatHmForHint(normalStart, lateEnd)}打卡为迟到`
}

function getOutPunchHint(row: any) {
  const earlyStart = row?.out_start || '--:--'
  const earlyEndRaw = row?.clock_out ? minutesToHm(hmToMinutes(row.clock_out) - 1) : '--:--'
  const normalStart = row?.clock_out || '--:--'
  const normalEnd = row?.out_end || '--:--'
  return `${earlyStart}-${formatHmForHint(earlyStart, earlyEndRaw)}打卡为早退，${formatHmForHint(earlyStart, normalStart)}-${formatHmForHint(earlyStart, normalEnd)}打卡为正常`
}

function openSegmentEditor(segment?: any, index = -1) {
  editingSegmentIndex.value = index
  segmentDraft.value = defaultTimeSegment(segment || {})
  restTimeDraft.value = Array.isArray(segmentDraft.value.rest_periods) && segmentDraft.value.rest_periods.length
    ? segmentDraft.value.rest_periods.map((x: any) => ({ start: x.start || '', end: x.end || '' }))
    : (segmentDraft.value.rest_start && segmentDraft.value.rest_end ? [{ start: segmentDraft.value.rest_start, end: segmentDraft.value.rest_end }] : [{ start: '', end: '' }])
  punchWindowDraft.value = rebuildPunchWindowsFromSegment(segmentDraft.value)
  halfDayDraft.value = {
    am: Array.isArray(segmentDraft.value.half_day_am) && segmentDraft.value.half_day_am.length === 2 ? [...segmentDraft.value.half_day_am] : ['09:00', '13:00'],
    pm: Array.isArray(segmentDraft.value.half_day_pm) && segmentDraft.value.half_day_pm.length === 2 ? [...segmentDraft.value.half_day_pm] : ['13:00', '17:00'],
  }
  workCalendarPreviewMonth.value = new Date().toISOString().slice(0, 7)
  workCalendarPreviewVisible.value = false
  restTimeDialogVisible.value = false
  punchWindowDialogVisible.value = false
  halfDayDialogVisible.value = false
  copySegmentDialogVisible.value = false
  segmentEditorVisible.value = true
}

function openRestTimeDialog() {
  restTimeDraft.value = Array.isArray(segmentDraft.value.rest_periods) && segmentDraft.value.rest_periods.length
    ? segmentDraft.value.rest_periods.map((x: any) => ({ start: x.start || '', end: x.end || '' }))
    : (segmentDraft.value.rest_start && segmentDraft.value.rest_end ? [{ start: segmentDraft.value.rest_start, end: segmentDraft.value.rest_end }] : [{ start: '', end: '' }])
  restTimeDialogVisible.value = true
}

function addRestPeriod() {
  if (!Array.isArray(restTimeDraft.value)) restTimeDraft.value = []
  restTimeDraft.value.push({ start: '', end: '' })
}

function removeRestPeriod(index: number) {
  if (!Array.isArray(restTimeDraft.value)) return
  restTimeDraft.value.splice(index, 1)
  if (!restTimeDraft.value.length) restTimeDraft.value.push({ start: '', end: '' })
}

function saveRestTime() {
  const cleaned = (restTimeDraft.value || []).filter((x) => x.start || x.end)
  for (const item of cleaned) {
    if (!item.start || !item.end) {
      ElMessage.warning('休息时间请完整填写开始和结束')
      return
    }
  }
  segmentDraft.value.rest_periods = cleaned
  segmentDraft.value.rest_start = cleaned[0]?.start || ''
  segmentDraft.value.rest_end = cleaned[0]?.end || ''
  restTimeDialogVisible.value = false
}

function openPunchWindowDialog() {
  // 对齐企微示例：首次打开时，补一条“工作时段2”
  if (!Array.isArray(segmentDraft.value.extra_periods)) segmentDraft.value.extra_periods = []
  if (segmentDraft.value.extra_periods.length === 0) {
    segmentDraft.value.extra_periods.push({
      clock_in: '18:00',
      clock_out: '19:00',
      check_in_required: true,
      check_out_required: true,
    })
  }
  punchWindowDraft.value = rebuildPunchWindowsFromSegment(segmentDraft.value)
  punchWindowDialogVisible.value = true
}

function removePunchWindowRow(index: number) {
  if (index <= 0) return
  if (!Array.isArray(segmentDraft.value.extra_periods)) return
  segmentDraft.value.extra_periods.splice(index - 1, 1)
  punchWindowDraft.value = rebuildPunchWindowsFromSegment(segmentDraft.value)
}

function savePunchWindow() {
  for (const row of (punchWindowDraft.value || [])) {
    if (!row.in_start || !row.in_end || !row.out_start || !row.out_end) {
      ElMessage.warning('请完整填写可打卡时段')
      return
    }
  }
  segmentDraft.value.punch_windows = punchWindowDraft.value.map((x) => ({
    in_start: x.in_start,
    in_end: x.in_end,
    out_start: x.out_start,
    out_end: x.out_end,
  }))
  segmentDraft.value.punch_start = punchWindowDraft.value[0]?.in_start || segmentDraft.value.punch_start
  segmentDraft.value.punch_end = punchWindowDraft.value[punchWindowDraft.value.length - 1]?.out_end || segmentDraft.value.punch_end
  punchWindowDialogVisible.value = false
}

function openHalfDayDialog() {
  halfDayDraft.value = {
    am: Array.isArray(segmentDraft.value.half_day_am) && segmentDraft.value.half_day_am.length === 2 ? [...segmentDraft.value.half_day_am] : ['09:00', '13:00'],
    pm: Array.isArray(segmentDraft.value.half_day_pm) && segmentDraft.value.half_day_pm.length === 2 ? [...segmentDraft.value.half_day_pm] : ['13:00', '17:00'],
  }
  halfDayDialogVisible.value = true
}

function saveHalfDay() {
  if (!Array.isArray(halfDayDraft.value.am) || halfDayDraft.value.am.length !== 2 || !Array.isArray(halfDayDraft.value.pm) || halfDayDraft.value.pm.length !== 2) {
    ElMessage.warning('请完整填写半天工作时间')
    return
  }
  segmentDraft.value.half_day_am = [...halfDayDraft.value.am]
  segmentDraft.value.half_day_pm = [...halfDayDraft.value.pm]
  halfDayDialogVisible.value = false
}

function addWorkPeriodFromEditor() {
  if (!Array.isArray(segmentDraft.value.extra_periods)) segmentDraft.value.extra_periods = []
  segmentDraft.value.extra_periods.push({
    clock_in: segmentDraft.value.clock_in || '09:00',
    clock_out: segmentDraft.value.clock_out || '17:00',
    check_in_required: true,
    check_out_required: true,
  })
  punchWindowDraft.value = rebuildPunchWindowsFromSegment(segmentDraft.value)
}

function removeWorkPeriodFromEditor(index: number) {
  if (!Array.isArray(segmentDraft.value.extra_periods)) return
  segmentDraft.value.extra_periods.splice(index, 1)
  punchWindowDraft.value = rebuildPunchWindowsFromSegment(segmentDraft.value)
}

function copyFromExistingRule() {
  copySegmentSelectedKeys.value = []
  copySegmentDialogVisible.value = true
}

const copySegmentOptions = computed(() => {
  const out: Array<{ key: string, ruleName: string, title: string, segment: any }> = []
  for (const r of (rules.value || [])) {
    if (ruleForm.value.id && r.id === ruleForm.value.id) continue
    const extra = r.extra || r.extra_config || {}
    const segments = Array.isArray(extra.time_segments) ? extra.time_segments : []
    segments.forEach((s: any, idx: number) => {
      out.push({
        key: `${r.id}-${idx}`,
        ruleName: r.name,
        title: `${formatSegmentWeekdays(defaultTimeSegment(s))} | ${defaultTimeSegment(s).clock_in} - ${defaultTimeSegment(s).clock_out}`,
        segment: defaultTimeSegment(s),
      })
    })
  }
  return out
})

function confirmCopySegments() {
  const keys = new Set(copySegmentSelectedKeys.value || [])
  const selected = copySegmentOptions.value.filter((x) => keys.has(x.key))
  if (!selected.length) {
    ElMessage.warning('请选择要复制的上下班时间')
    return
  }
  if (selected.length > 7) {
    ElMessage.warning('最多复制7条上下班时间')
    return
  }
  ruleForm.value.extra.time_segments = selected.map((x, idx) => defaultTimeSegment({ ...x.segment, name: buildSegmentName(idx) }))
  copySegmentDialogVisible.value = false
  segmentEditorVisible.value = false
  ElMessage.success('复制成功')
}

function saveSegmentEditor() {
  if (!String(segmentDraft.value.name || '').trim()) {
    const idx = editingSegmentIndex.value >= 0
      ? editingSegmentIndex.value
      : (ruleForm.value.extra.time_segments?.length || 0)
    segmentDraft.value.name = buildSegmentName(idx)
  }
  if (segmentDraft.value.biweekly_enabled) {
    if (!(segmentDraft.value.this_week_weekdays || []).length || !(segmentDraft.value.next_week_weekdays || []).length) {
      ElMessage.warning('开启双周重复后，本周和下周都需要至少选择一个工作日')
      return
    }
    segmentDraft.value.weekdays = Array.from(new Set([...(segmentDraft.value.this_week_weekdays || []), ...(segmentDraft.value.next_week_weekdays || [])]))
  } else if (!(segmentDraft.value.weekdays || []).length) {
    ElMessage.warning('请至少选择一个工作日')
    return
  }
  if (!segmentDraft.value.clock_in || !segmentDraft.value.clock_out) {
    ElMessage.warning('请填写完整上下班时间')
    return
  }
  for (const period of (segmentDraft.value.extra_periods || [])) {
    if (!period.clock_in || !period.clock_out) {
      ElMessage.warning('新增时段请填写完整上下班时间')
      return
    }
  }
  if ((segmentDraft.value.rest_start && !segmentDraft.value.rest_end) || (!segmentDraft.value.rest_start && segmentDraft.value.rest_end)) {
    ElMessage.warning('休息时间请完整填写开始和结束')
    return
  }
  if (Array.isArray(segmentDraft.value.rest_periods)) {
    for (const item of segmentDraft.value.rest_periods) {
      if ((item?.start && !item?.end) || (!item?.start && item?.end)) {
        ElMessage.warning('休息时间请完整填写开始和结束')
        return
      }
    }
  }
  if (editingSegmentIndex.value >= 0) {
    ruleForm.value.extra.time_segments.splice(editingSegmentIndex.value, 1, defaultTimeSegment(segmentDraft.value))
  } else {
    ruleForm.value.extra.time_segments.push(defaultTimeSegment(segmentDraft.value))
  }
  segmentEditorVisible.value = false
}

function addTimeSegment(openEditor = false) {
  if (!Array.isArray(ruleForm.value.extra.time_segments)) ruleForm.value.extra.time_segments = []
  if (openEditor) {
    openSegmentEditor(defaultTimeSegment({ name: buildSegmentName(ruleForm.value.extra.time_segments.length) }), -1)
    return
  }
  ruleForm.value.extra.time_segments.push(defaultTimeSegment({ name: buildSegmentName(ruleForm.value.extra.time_segments.length) }))
}

function removeTimeSegment(index: number) {
  if (!Array.isArray(ruleForm.value.extra.time_segments)) return
  ruleForm.value.extra.time_segments.splice(index, 1)
}

function openSpecialDateDialog(type: 'must' | 'no') {
  specialDateMode.value = type
  specialDateDraft.value = defaultSpecialDateConfig()
  specialDateDialogVisible.value = true
}

function addSpecialDateTime() {
  specialDateDraft.value.punch_times.push({ start: '09:00', end: '18:00' })
}

function removeSpecialDateTime(index: number) {
  specialDateDraft.value.punch_times.splice(index, 1)
  if (!specialDateDraft.value.punch_times.length) {
    specialDateDraft.value.punch_times.push({ start: '09:00', end: '18:00' })
  }
}

function saveSpecialDate() {
  const draft = defaultSpecialDateConfig(specialDateDraft.value)
  if (draft.mode === 'single' && !draft.date) {
    ElMessage.warning('请选择日期')
    return
  }
  if (draft.mode === 'range' && (!Array.isArray(draft.date_range) || draft.date_range.length !== 2)) {
    ElMessage.warning('请选择日期范围')
    return
  }
  if (!String(draft.reason || '').trim()) {
    ElMessage.warning('请填写特殊事由')
    return
  }
  if (ruleForm.value.extra?.rule_type === 'free' && specialDateMode.value === 'no') {
    draft.punch_times = []
  }
  const key = specialDateMode.value === 'must' ? 'special_must_dates' : 'special_no_dates'
  if (!Array.isArray(ruleForm.value.extra[key])) ruleForm.value.extra[key] = []
  ruleForm.value.extra[key].push(draft)
  specialDateDialogVisible.value = false
}

function summarizeSpecialDate(item: any) {
  const modeText = item.mode === 'range'
    ? `${(item.date_range || [])[0] || '-'}~${(item.date_range || [])[1] || '-'}`
    : (item.date || '-')
  const repeatText = item.repeat === 'yearly' ? '每年重复' : item.repeat === 'monthly' ? '每月重复' : '不重复'
  const timeText = Array.isArray(item.punch_times)
    ? item.punch_times.map((x: any) => `${x.start || '--:--'}-${x.end || '--:--'}`).join('，')
    : '--:--'
  return `${modeText} / ${repeatText} / ${timeText}${item.reason ? ` / ${item.reason}` : ''}`
}

function removeSpecialDate(type: 'must' | 'no', index: number) {
  const key = type === 'must' ? 'special_must_dates' : 'special_no_dates'
  ruleForm.value.extra[key] = (ruleForm.value.extra[key] || []).filter((_: any, idx: number) => idx !== index)
}

function openShiftTemplateDialog() {
  shiftTemplateDialogVisible.value = true
}

function templateValueKey(tpl: any) {
  return String(tpl?.id || tpl?.name || '')
}

function getTemplateByKey(key: string) {
  return (ruleForm.value.extra?.shift_templates || []).find((x: any) => templateValueKey(x) === String(key))
}

function shiftCellKey(employeeId: number, dayKey: string) {
  return `${employeeId}_${dayKey}`
}

async function loadShiftAssignments() {
  const month = shiftArrangeMonth.value
  const ids = Array.isArray(ruleForm.value.extra?.assigned_employee_ids) ? ruleForm.value.extra.assigned_employee_ids : []
  shiftAssignMap.value = {}
  if (!month || !ids.length || !ruleForm.value.id) return
  shiftArrangeLoading.value = true
  try {
    const list = await get('/attendance/shifts', { month }).catch(() => [])
    const rows = Array.isArray(list) ? list : (list?.items || list?.data || [])
    const picked = rows.filter((r: any) => ids.includes(r.employee_id))
    for (const row of picked) {
      const dateKey = String(row.date || '').slice(0, 10)
      if (!dateKey) continue
      const key = shiftCellKey(Number(row.employee_id), dateKey)
      if (String(row.shift_type || '') === 'rest') {
        const restTpl = (ruleForm.value.extra.shift_templates || []).find((t: any) => t.name === '休息')
        if (restTpl) shiftAssignMap.value[key] = templateValueKey(restTpl)
        continue
      }
      const matched = (ruleForm.value.extra.shift_templates || []).find(
        (t: any) => normalizeTime(t.clock_in) === normalizeTime(row.start_time) && normalizeTime(t.clock_out) === normalizeTime(row.end_time),
      )
      if (matched) shiftAssignMap.value[key] = templateValueKey(matched)
    }
  } finally {
    shiftArrangeLoading.value = false
  }
}

async function openShiftArrangeDialog() {
  if (!Array.isArray(ruleForm.value.extra.shift_templates) || !ruleForm.value.extra.shift_templates.length) {
    ruleForm.value.extra.shift_templates = defaultShiftTemplatePreset()
  }
  shiftArrangeDialogVisible.value = true
  await loadShiftAssignments()
}

function openShiftClassDrawer() {
  if (!Array.isArray(ruleForm.value.extra.shift_templates)) ruleForm.value.extra.shift_templates = []
  shiftClassDrawerVisible.value = true
}

function openShiftClassEditor(index = -1) {
  shiftClassEditingIndex.value = index
  if (index >= 0) {
    const current = ruleForm.value.extra.shift_templates[index]
    shiftClassDraft.value = defaultShiftTemplate(current)
  } else {
    shiftClassDraft.value = defaultShiftTemplate({ name: '', clock_in: '09:00', clock_out: '17:00', color: '#BFDBFE' })
  }
  shiftClassEditorVisible.value = true
}

function removeShiftClass(index: number) {
  if (!Array.isArray(ruleForm.value.extra.shift_templates)) return
  ruleForm.value.extra.shift_templates.splice(index, 1)
}

function saveShiftClass() {
  const draft = defaultShiftTemplate(shiftClassDraft.value)
  if (!String(draft.name || '').trim()) {
    ElMessage.warning('请填写班次名称')
    return
  }
  if (!draft.clock_in || !draft.clock_out) {
    ElMessage.warning('请完整填写上下班时间')
    return
  }
  if (!Array.isArray(ruleForm.value.extra.shift_templates)) ruleForm.value.extra.shift_templates = []
  if (shiftClassEditingIndex.value >= 0) {
    ruleForm.value.extra.shift_templates.splice(shiftClassEditingIndex.value, 1, draft)
  } else {
    ruleForm.value.extra.shift_templates.push(draft)
  }
  shiftClassEditorVisible.value = false
}

async function saveShiftAssignment(employeeId: number, dayKey: string) {
  const mapKey = shiftCellKey(employeeId, dayKey)
  const selectedKey = shiftAssignMap.value[mapKey]
  const tpl = getTemplateByKey(selectedKey)
  if (!tpl || !ruleForm.value.id) return
  if (shiftAssignSavingKeys.value.has(mapKey)) return
  shiftAssignSavingKeys.value.add(mapKey)
  try {
    const isRest = String(tpl.name || '') === '休息'
    await post('/attendance/shifts', {
      employee_id: employeeId,
      date: dayKey,
      rule_id: Number(ruleForm.value.id),
      shift_type: isRest ? 'rest' : (hmToMinutes(tpl.clock_out) < hmToMinutes(tpl.clock_in) ? 'night_shift' : 'day_shift'),
      start_time: isRest ? null : tpl.clock_in,
      end_time: isRest ? null : tpl.clock_out,
    })
  } catch {
    ElMessage.error('排班保存失败')
  } finally {
    shiftAssignSavingKeys.value.delete(mapKey)
  }
}

async function persistAllShiftAssignments(ruleId: number) {
  if (!ruleId) return
  const pairs = Object.entries(shiftAssignMap.value || {})
  if (!pairs.length) return
  for (const [mapKey, templateKey] of pairs) {
    const [employeeIdText, dayKey] = mapKey.split('_')
    const employeeId = Number(employeeIdText)
    if (!employeeId || !dayKey) continue
    const tpl = getTemplateByKey(templateKey)
    if (!tpl) continue
    const isRest = String(tpl.name || '') === '休息'
    await post('/attendance/shifts', {
      employee_id: employeeId,
      date: dayKey,
      rule_id: Number(ruleId),
      shift_type: isRest ? 'rest' : (hmToMinutes(tpl.clock_out) < hmToMinutes(tpl.clock_in) ? 'night_shift' : 'day_shift'),
      start_time: isRest ? null : tpl.clock_in,
      end_time: isRest ? null : tpl.clock_out,
    }).catch(() => null)
  }
}

function addShiftTemplate() {
  const name = shiftTemplateDraft.value.name.trim()
  if (!name || !shiftTemplateDraft.value.clock_in || !shiftTemplateDraft.value.clock_out) {
    ElMessage.warning('请填写完整班次模板信息')
    return
  }
  if (hmToMinutes(shiftTemplateDraft.value.clock_out) <= hmToMinutes(shiftTemplateDraft.value.clock_in)) {
    ElMessage.warning('下班时间必须晚于上班时间')
    return
  }
  if (!Array.isArray(ruleForm.value.extra.shift_templates)) ruleForm.value.extra.shift_templates = []
  ruleForm.value.extra.shift_templates.push({
    name,
    clock_in: shiftTemplateDraft.value.clock_in,
    clock_out: shiftTemplateDraft.value.clock_out,
  })
  shiftTemplateDraft.value = { name: '', clock_in: '09:30', clock_out: '18:30' }
}

function removeShiftTemplate(index: number) {
  if (!Array.isArray(ruleForm.value.extra.shift_templates)) return
  ruleForm.value.extra.shift_templates.splice(index, 1)
}

function openLocationDialog() {
  locationDraft.value = {
    country: '中国大陆',
    name: '',
    radius: 300,
    latitude: '',
    longitude: '',
  }
  locationDialogVisible.value = true
}

function openWifiDialog() {
  wifiDraft.value = { name: '', bssid: '' }
  wifiBssidParts.value = ['', '', '', '', '', '']
  wifiDialogVisible.value = true
}

function normalizeWifiBssidPart(part: string) {
  return String(part || '').toUpperCase().replace(/[^0-9A-F]/g, '').slice(0, 2)
}

function parseBssidParts(rawText: string) {
  const merged = String(rawText || '').toUpperCase().replace(/[^0-9A-F]/g, '')
  if (!merged.length) return ['', '', '', '', '', '']
  const next = ['', '', '', '', '', '']
  for (let i = 0; i < 6; i += 1) {
    next[i] = merged.slice(i * 2, i * 2 + 2)
  }
  return next
}

function onWifiBssidInput(index: number, value: string) {
  const text = String(value || '')
  if (text.includes(':') || text.includes('-')) {
    const parsed = parseBssidParts(text)
    wifiBssidParts.value = parsed.map((x) => normalizeWifiBssidPart(x))
    return
  }
  wifiBssidParts.value[index] = normalizeWifiBssidPart(text)
}

function onWifiBssidPaste(event: ClipboardEvent) {
  const text = event.clipboardData?.getData('text') || ''
  if (!text) return
  const parsed = parseBssidParts(text)
  if (!parsed.some((x) => x)) return
  event.preventDefault()
  wifiBssidParts.value = parsed.map((x) => normalizeWifiBssidPart(x))
}

function mockImportEntry(type: string) {
  const tips: Record<string, string> = {
    location_phone: '已打开“手机上报”入口（演示文案）',
    location_batch: '已打开“地点批量导入”入口（演示文案）',
    location_tpl: '已触发“地点模板下载”入口（演示文案）',
    wifi_phone: '已打开“手机上报”入口（演示文案）',
    wifi_batch: '已打开“Wi-Fi 批量导入”入口（演示文案）',
    wifi_tpl: '已触发“Wi-Fi 模板下载”入口（演示文案）',
  }
  ElMessage.info(tips[type] || '入口已打开')
}

function normalizeCoordinate(value: unknown) {
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return n
}

const LOCATION_DIALOG_BUILD_TAG = 'loc-dialog-2026-04-17-v2'

async function searchTencentLocationSuggestions(query: string) {
  const region = locationDraft.value.country === '中国大陆' ? '全国' : locationDraft.value.country
  const res = await get('/attendance/maps/tencent/place-suggestions', {
    keyword: query,
    region,
    limit: 10,
  }, {
    skipErrorReport: true,
    skipDefaultErrorHandler: true,
  }).catch((err: any) => {
    ElMessage.warning(err?.response?.data?.detail || '地点联想服务暂不可用')
    return { items: [] }
  })
  const list = Array.isArray(res?.items) ? res.items : []
  return list
    .map((item: any) => ({
      value: item?.value || item?.title || item?.address || query,
      title: item?.title || '',
      address: item?.address || '',
      latitude: Number(item?.latitude),
      longitude: Number(item?.longitude),
    }))
    .filter((x: any) => Number.isFinite(x.latitude) && Number.isFinite(x.longitude))
}

async function queryLocationSuggestions(queryString: string, cb: (items: any[]) => void) {
  const query = String(queryString || '').trim()
  if (!query) {
    cb([])
    return
  }
  const currentToken = ++locationSuggestionToken.value
  try {
    const list = await searchTencentLocationSuggestions(query)
    if (currentToken !== locationSuggestionToken.value) return
    cb(list.slice(0, 10))
  } catch {
    if (currentToken !== locationSuggestionToken.value) return
    cb([])
  }
}

function handleLocationSuggestionSelect(item: any) {
  const latitude = normalizeCoordinate(item?.latitude)
  const longitude = normalizeCoordinate(item?.longitude)
  if (latitude === null || longitude === null) {
    ElMessage.warning('该地点未返回有效坐标，请换一个联想项')
    return
  }
  locationDraft.value.name = item?.title || item?.value || locationDraft.value.name
  locationDraft.value.latitude = String(latitude)
  locationDraft.value.longitude = String(longitude)
  addLocation()
}

function addLocation() {
  const name = locationDraft.value.name?.trim()
  if (!name) {
    ElMessage.warning('请输入打卡地点')
    return
  }
  const latitude = normalizeCoordinate(locationDraft.value.latitude)
  const longitude = normalizeCoordinate(locationDraft.value.longitude)
  if (latitude === null || longitude === null) {
    ElMessage.warning('请先解析地址坐标或使用当前位置')
    return
  }
  if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
    ElMessage.warning('经纬度超出有效范围')
    return
  }
  if (!Array.isArray(ruleForm.value.extra.locations)) ruleForm.value.extra.locations = []
  ruleForm.value.extra.locations.push({
    name,
    radius: Number(locationDraft.value.radius || 300),
    latitude,
    longitude,
  })
  locationDraft.value = { country: '中国大陆', name: '', radius: 300, latitude: '', longitude: '' }
  locationDialogVisible.value = false
}

function removeLocation(index: number) {
  ruleForm.value.extra.locations.splice(index, 1)
}

function addWifi() {
  const name = wifiDraft.value.name?.trim()
  if (!name) {
    ElMessage.warning('请输入 Wi-Fi 名称')
    return
  }
  const bssidParts = (wifiBssidParts.value || []).map((x) => normalizeWifiBssidPart(x))
  if (bssidParts.length !== 6 || bssidParts.some((x) => !/^[0-9A-F]{2}$/.test(x))) {
    ElMessage.warning('请填写完整且有效的 BSSID（6组两位十六进制字符）')
    return
  }
  if (!Array.isArray(ruleForm.value.extra.wifi_list)) ruleForm.value.extra.wifi_list = []
  ruleForm.value.extra.wifi_list.push({
    name,
    bssid: bssidParts.join(':'),
  })
  wifiDraft.value = { name: '', bssid: '' }
  wifiBssidParts.value = ['', '', '', '', '', '']
  wifiDialogVisible.value = false
}

function removeWifi(index: number) {
  ruleForm.value.extra.wifi_list.splice(index, 1)
}

async function fetchRules() {
  ruleLoading.value = true
  try {
    const params: any = {}
    if (ruleStatusFilter.value !== null) params.is_active = ruleStatusFilter.value
    const res = await get('/attendance/rules', params)
    const list = res.items || res.data || res || []
    rules.value = list.map(normalizeRule)
  } catch { rules.value = [] }
  finally { ruleLoading.value = false }
}

function openRuleDialog(row?: any) {
  if (row) {
    ruleForm.value = normalizeRule(row)
  } else {
    ruleForm.value = defaultRuleForm()
    ruleForm.value.extra.scope_mode = 'project'
  }
  assigneePickerVisible.value = false
  assigneeKeyword.value = ''
  noPunchSelectorVisible.value = false
  reportTargetSelectorVisible.value = false
  assistManagerSelectorVisible.value = false
  noPunchKeyword.value = ''
  reportTargetKeyword.value = ''
  assistManagerKeyword.value = ''
  segmentEditorVisible.value = false
  restTimeDialogVisible.value = false
  punchWindowDialogVisible.value = false
  halfDayDialogVisible.value = false
  copySegmentDialogVisible.value = false
  workCalendarPreviewVisible.value = false
  holidayCalendarVisible.value = false
  holidayCalendarServerMap.value = {}
  holidayCalendarLoading.value = false
  specialDateDialogVisible.value = false
  shiftTemplateDialogVisible.value = false
  restdaySettingDialogVisible.value = false
  leavePunchSettingDialogVisible.value = false
  overtimeRuleDialogVisible.value = false
  overtimeDurationDialogVisible.value = false
  shiftArrangeDialogVisible.value = false
  shiftClassDrawerVisible.value = false
  shiftClassEditorVisible.value = false
  locationDialogVisible.value = false
  wifiDialogVisible.value = false
  locationDraft.value = {
    country: '中国大陆',
    name: '',
    radius: 300,
    latitude: '',
    longitude: '',
  }
  wifiDraft.value = { name: '', bssid: '' }
  wifiBssidParts.value = ['', '', '', '', '', '']
  shiftTemplateDraft.value = { name: '', clock_in: '09:30', clock_out: '18:30' }
  shiftArrangeMonth.value = new Date().toISOString().slice(0, 7)
  shiftArrangeKeyword.value = ''
  shiftAssignMap.value = {}
  restdaySettingDraft.value = { shift_mode: 'multi', interval_minutes: 5 }
  leavePunchSettingDraft.value = {
    need_punch: !!ruleForm.value.extra?.leave_need_punch_before_after,
    before_max_advance: String(ruleForm.value.extra?.leave_before_max_advance || '无限制'),
    after_max_delay: String(ruleForm.value.extra?.leave_after_max_delay || '无限制'),
  }
  leavePunchSettingSnapshot.value = JSON.stringify(leavePunchSettingDraft.value)
  overtimeRuleDraft.value = null
  overtimeDurationDraft.value = null
  syncOvertimeLegacySummary(ruleForm.value.extra)
  ruleDialogVisible.value = true
}

function hmToMinutes(v: string) {
  if (!v || !v.includes(':')) return 0
  const [h, m] = v.split(':').map(Number)
  return h * 60 + m
}

function buildRulePayload() {
  const extra = { ...defaultRuleExtra(), ...(ruleForm.value.extra || {}) }
  syncOvertimeLegacySummary(extra)
  extra.rule_type = extra.rule_type || inferRuleType(ruleForm.value.work_hour_type)
  extra.free_day_start_time = normalizeTime(extra.free_day_start_time || '') || '05:00'
  extra.free_workdays = Array.isArray(extra.free_workdays) && extra.free_workdays.length ? extra.free_workdays : [1, 2, 3, 4, 5]
  extra.leave_need_punch_before_after = !!extra.leave_need_punch_before_after
  extra.leave_punch_rule = extra.leave_need_punch_before_after ? '离岗返岗打卡' : '无需打卡'
  extra.leave_retroactive_limit_enabled = !!extra.leave_retroactive_limit_enabled
  extra.leave_retroactive_limit_months = Math.max(0, Math.min(12, Number(extra.leave_retroactive_limit_months || 0)))
  extra.leave_retroactive_cutoff_day = Math.max(1, Math.min(31, Number(extra.leave_retroactive_cutoff_day || 10)))
  extra.leave_retroactive_block_locked_summary = extra.leave_retroactive_block_locked_summary !== false
  extra.leave_retroactive_finance_mode = 'approval_month_adjustment'
  if (!leavePunchTimeLimitOptions.includes(String(extra.leave_before_max_advance || ''))) extra.leave_before_max_advance = '无限制'
  if (!leavePunchTimeLimitOptions.includes(String(extra.leave_after_max_delay || ''))) extra.leave_after_max_delay = '无限制'
  extra.enable_patch_apply = !!extra.enable_patch_apply
  extra.patch_types = Array.isArray(extra.patch_types)
    ? extra.patch_types.map((item: any) => String(item)).filter((item: string) => patchTypeOptions.includes(item))
    : []
  if (!extra.patch_types.length) extra.patch_types = [...patchTypeOptions]
  if (!patchTimeLimitOptions.includes(String(extra.patch_time_limit || ''))) extra.patch_time_limit = '无限制'
  if (!patchMonthLimitOptions.includes(String(extra.patch_month_limit || ''))) extra.patch_month_limit = '无限制'
  if (!patchDeadlineOptions.includes(String(extra.patch_deadline || ''))) extra.patch_deadline = '不设置'
  extra.time_segments = (extra.time_segments || []).map((segment: any) => defaultTimeSegment(segment))
  if (extra.rule_type === 'free') {
    extra.time_segments = [defaultTimeSegment({
      name: '自由上下班',
      weekdays: [...extra.free_workdays],
      check_in_required: false,
      check_out_required: false,
      clock_in: '00:00',
      clock_out: '23:59',
      punch_start: extra.free_day_start_time,
      punch_end: minutesToHm(hmToMinutes(extra.free_day_start_time) - 1),
      rest_periods: [],
      rest_start: '',
      rest_end: '',
      flex_mode: 'none',
    })]
  }
  extra.shift_templates = (extra.shift_templates || []).map((tpl: any) => defaultShiftTemplate(tpl))
  if (extra.scope_mode === 'all') {
    extra.scope_type = 'all'
    extra.department_ids = []
    extra.attendance_group = ''
  } else {
    if (extra.scope_custom_types?.includes('department') && (extra.department_ids || []).length) {
      extra.scope_type = 'department'
    } else if (extra.scope_custom_types?.includes('group') && (extra.attendance_groups || []).length) {
      extra.scope_type = 'group'
    } else {
      extra.scope_type = 'all'
    }
    extra.attendance_group = (extra.attendance_groups || [])[0] || ''
  }
  const firstSegment = extra.time_segments[0]
  const gpsLocation = (extra.locations || []).find((loc: any) => {
    const lat = Number(loc?.latitude)
    const lng = Number(loc?.longitude)
    return Number.isFinite(lat) && Number.isFinite(lng)
  })
  const gpsLat = gpsLocation ? Number(gpsLocation.latitude) : null
  const gpsLng = gpsLocation ? Number(gpsLocation.longitude) : null
  const gpsRadius = gpsLocation ? Number(gpsLocation.radius || 300) : null
  const primaryWifi = (extra.wifi_list || [])[0]
  const requirePhoto = !!extra.photo_each_punch
  const department_ids = extra.scope_custom_types?.includes?.('department') ? (extra.department_ids || []) : []
  const workHourType = workHourTypeFromRuleType(extra.rule_type)
  const firstShiftTpl = (extra.shift_templates || []).find((x: any) => x.name !== '休息') || (extra.shift_templates || [])[0]
  const freeLimitHours = String(extra.free_work_hours_mode || '').startsWith('limit:')
    ? Number(String(extra.free_work_hours_mode).split(':')[1] || 0)
    : null
  return {
    name: ruleForm.value.name,
    work_hour_type: workHourType,
    clock_in_time: extra.rule_type === 'free'
      ? null
      : (extra.rule_type === 'shift'
        ? (firstShiftTpl?.clock_in || firstSegment?.clock_in || ruleForm.value.clock_in_time || null)
        : (firstSegment?.clock_in || ruleForm.value.clock_in_time || null)),
    clock_out_time: extra.rule_type === 'free'
      ? null
      : (extra.rule_type === 'shift'
        ? (firstShiftTpl?.clock_out || firstSegment?.clock_out || ruleForm.value.clock_out_time || null)
        : (firstSegment?.clock_out || ruleForm.value.clock_out_time || null)),
    flexible_minutes: Number(ruleForm.value.flexible_minutes || 0),
    work_hours_per_day: extra.rule_type === 'free'
      ? Number((freeLimitHours && freeLimitHours > 0) ? freeLimitHours : 8)
      : Number(ruleForm.value.work_hours_per_day || 8),
    overtime_weekday_rate: Number(ruleForm.value.overtime_weekday_rate || 1.5),
    overtime_weekend_rate: Number(ruleForm.value.overtime_weekend_rate || 2),
    overtime_holiday_rate: Number(ruleForm.value.overtime_holiday_rate || 3),
    require_gps: ruleForm.value.check_methods.includes('GPS') && gpsLat !== null && gpsLng !== null,
    require_wifi: ruleForm.value.check_methods.includes('WiFi') && !!primaryWifi?.name,
    require_photo: requirePhoto,
    gps_latitude: gpsLat,
    gps_longitude: gpsLng,
    gps_radius_meters: gpsRadius,
    wifi_ssid: primaryWifi?.name || null,
    department_id: department_ids[0] || null,
    priority: Number(ruleForm.value.priority || 100),
    effective_date: ruleForm.value.effective_date,
    is_active: !!ruleForm.value.is_active,
    extra_config: {
      ...extra,
      department_ids,
      check_methods: Array.from(new Set([...(ruleForm.value.check_methods || []).filter((method: string) => method !== '拍照'), ...(requirePhoto ? ['拍照'] : [])])),
    },
  }
}

async function submitRule() {
  const el = ruleFormRef.value
  if (!el) return
  if (!(await el.validate().catch(() => false))) return
  ruleForm.value.extra.assigned_employee_ids = normalizeSelectableAssigneeIds(ruleForm.value.extra.assigned_employee_ids || [])
  const extra = { ...defaultRuleExtra(), ...(ruleForm.value.extra || {}) }
  if (!String(ruleForm.value.name || '').trim()) {
    ElMessage.warning('请输入规则名称')
    return
  }
  if (extra.scope_mode === 'custom') {
    if (!extra.scope_custom_types?.length) {
      ElMessage.warning('请至少选择一个适用范围维度')
      return
    }
    if (extra.scope_custom_types.includes('department') && !(extra.department_ids || []).length) {
      ElMessage.warning('请选择适用部门')
      return
    }
    if (extra.scope_custom_types.includes('employee') && !(extra.member_ids || []).length) {
      ElMessage.warning('请选择指定成员')
      return
    }
    if (extra.scope_custom_types.includes('group') && !(extra.attendance_groups || []).length) {
      ElMessage.warning('请至少填写一个考勤组')
      return
    }
    if (extra.scope_custom_types.includes('tag') && !(extra.label_tags || []).length) {
      ElMessage.warning('请至少选择一个标签')
      return
    }
  }
  if (extra.enable_patch_apply && !(extra.patch_types || []).length) {
    ElMessage.warning('请至少选择一种可补类型')
    return
  }
  const punchChoiceMode = extra.punch_choice_mode || 'mobile'
  if (['mobile', 'both'].includes(punchChoiceMode)) {
    const hasLocation = Array.isArray(extra.locations) && extra.locations.length > 0
    const hasWifi = Array.isArray(extra.wifi_list) && extra.wifi_list.length > 0
    if (!hasLocation && !hasWifi) {
      ElMessage.warning('选择手机相关打卡方式时，请至少配置一个打卡位置或打卡WiFi')
      return
    }
    const hasGpsLocation = (extra.locations || []).some((loc: any) => {
      const lat = Number(loc?.latitude)
      const lng = Number(loc?.longitude)
      return Number.isFinite(lat) && Number.isFinite(lng)
    })
    if (hasLocation && !hasGpsLocation) {
      ElMessage.warning('打卡位置缺少真实坐标，请先解析地址坐标或使用当前位置')
      return
    }
  }
  if (extra.rule_type !== 'shift' && (!Array.isArray(extra.time_segments) || !extra.time_segments.length)) {
    ElMessage.warning('请至少配置一个打卡时段')
    return
  }
  if (extra.rule_type === 'free' && (!Array.isArray(extra.free_workdays) || !extra.free_workdays.length)) {
    ElMessage.warning('自由上下班请至少选择一个工作日')
    return
  }
  if (extra.rule_type === 'shift' && (!extra.shift_templates || !extra.shift_templates.length)) {
    ElMessage.warning('按排班上下班时，请至少配置一个班次')
    return
  }
  if (extra.rule_type !== 'free') {
    for (const segment of extra.time_segments) {
      if (!segment.clock_in || !segment.clock_out) {
        ElMessage.warning('请完整填写上下班时间')
        return
      }
      if (hmToMinutes(segment.clock_out) <= hmToMinutes(segment.clock_in)) {
        ElMessage.warning('下班时间必须晚于上班时间')
        return
      }
    }
  }
  ruleSubmitting.value = true
  try {
    const payload = buildRulePayload()
    let savedRuleId = Number(ruleForm.value.id || 0)
    const checkUrl = ruleForm.value.id
      ? `/attendance/rules/conflicts/check?exclude_rule_id=${ruleForm.value.id}`
      : '/attendance/rules/conflicts/check'
    const conflicts: any[] = await post(checkUrl, payload).catch(() => [])
    if (Array.isArray(conflicts) && conflicts.length > 0) {
      await ElMessageBox.confirm(
        `检测到可能冲突规则：${conflicts[0].name}，是否仍继续保存？`,
        '规则冲突提示',
        { type: 'warning', confirmButtonText: '继续保存', cancelButtonText: '取消' },
      ).catch(() => Promise.reject(new Error('cancel')))
    }
    if (ruleForm.value.id) {
      const updated = await put(`/attendance/rules/${ruleForm.value.id}`, payload)
      savedRuleId = Number(updated?.id || ruleForm.value.id || 0)
      ElMessage.success('规则更新成功')
    } else {
      const created = await post('/attendance/rules', payload)
      savedRuleId = Number(created?.id || 0)
      ElMessage.success('规则创建成功')
    }
    if (payload?.extra_config?.rule_type === 'shift' && payload.extra_config.scope_mode !== 'project') {
      await persistAllShiftAssignments(savedRuleId)
    }
    ruleDialogVisible.value = false
    await fetchRules()
  } catch (e: any) {
    if (String(e?.message || '').includes('cancel')) return
  } finally { ruleSubmitting.value = false }
}

async function toggleRuleStatus(row: any, isActiveValue: any) {
  const isActive = !!isActiveValue
  try {
    await request.patch(`/attendance/rules/${row.id}/toggle`, { is_active: isActive })
    row.is_active = isActive
    ElMessage.success(`规则已${isActive ? '启用' : '停用'}`)
  } catch {
    row.is_active = !isActive
  }
}

async function copyRule(row: any) {
  const name = `${row.name}-副本`
  try {
    await post(`/attendance/rules/${row.id}/copy`, { name })
    ElMessage.success('规则复制成功')
    await fetchRules()
  } catch {}
}

async function openRuleLogs(row: any) {
  currentRuleName.value = row.name
  ruleLogsVisible.value = true
  try {
    const res = await get(`/attendance/rules/${row.id}/logs`)
    ruleLogs.value = res.items || res.data || res || []
  } catch {
    ruleLogs.value = []
  }
}

// ============ 日报汇总 ============
const dailySubTab = ref<'overview' | 'detail'>('overview')
const dailyOverviewRows = ref<any[]>([])
const dailyDetailRows = ref<any[]>([])
const dailyLoading = ref(false)
const dailyDateRange = ref<string[]>([])
const dailyDept = ref<number | null>(null)
const dailyKeyword = ref('')
const dailyStatus = ref('')
const dailyRuleId = ref<number | null>(null)
const dailyIncludeRecentLeft = ref(true)
const dailyPage = reactive({ page: 1, page_size: 20, total: 0 })
const dailyReportSettingVisible = ref(false)
const dailySettingTab = ref<'overview' | 'detail'>('overview')
const dailySettingOpenGroups = ref<string[]>(['basic', 'attendance'])
const DAILY_REPORT_SETTINGS_KEY = 'attendanceDailyReportSettingsV1'

const dailyOverviewColumnGroups = [
  {
    key: 'basic',
    label: '基础信息',
    columns: [
      { key: 'department_name', label: '部门' },
      { key: 'position', label: '职务' },
    ],
  },
  {
    key: 'attendance',
    label: '考勤概况',
    columns: [
      { key: 'rule_name', label: '所属规则' },
      { key: 'shift_name', label: '班次' },
      { key: 'earliest_clock', label: '最早' },
      { key: 'latest_clock', label: '最晚' },
      { key: 'clock_count', label: '打卡次数(次)' },
      { key: 'standard_work_hours', label: '标准工作时长(小时)' },
      { key: 'actual_work_hours', label: '实际工作时长(小时)' },
      { key: 'leave_application', label: '假勤申请' },
      { key: 'attendance_result', label: '考勤结果' },
    ],
  },
  {
    key: 'abnormal',
    label: '异常统计',
    columns: [
      { key: 'anomaly_total', label: '异常合计(次)' },
      { key: 'late_count', label: '迟到次数(次)' },
      { key: 'late_minutes', label: '迟到时长(分钟)' },
      { key: 'early_count', label: '早退次数(次)' },
      { key: 'early_minutes', label: '早退时长(分钟)' },
      { key: 'absent_count', label: '旷工次数(次)' },
      { key: 'absent_minutes', label: '旷工时长(分钟)' },
      { key: 'missed_count', label: '缺卡次数(次)' },
      { key: 'location_abnormal_count', label: '地点异常(次)' },
      { key: 'device_abnormal_count', label: '设备异常(次)' },
    ],
  },
  {
    key: 'outside',
    label: '外出打卡',
    columns: [
      { key: 'outside_clock_count', label: '外出打卡次数(次)' },
      { key: 'outside_earliest', label: '最早' },
      { key: 'outside_latest', label: '最晚' },
    ],
  },
  {
    key: 'overtime',
    label: '加班统计',
    columns: [
      { key: 'overtime_status', label: '加班状态' },
      { key: 'overtime_hours', label: '加班时长(小时)' },
      { key: 'weekday_ot_to_rest', label: '工作日加班计为调休(小时)' },
      { key: 'weekday_ot_to_pay', label: '工作日加班计为加班费(小时)' },
      { key: 'weekend_ot_to_rest', label: '休息日加班计为调休(小时)' },
      { key: 'weekend_ot_to_pay', label: '休息日加班计为加班费(小时)' },
      { key: 'holiday_ot_to_rest', label: '节假日加班计为调休(小时)' },
      { key: 'holiday_ot_to_pay', label: '节假日加班计为加班费(小时)' },
    ],
  },
  {
	    key: 'leave',
	    label: '假勤统计',
	    columns: [
	      { key: 'approve_punch_count', label: '审批打卡次数(次)' },
      { key: 'field_work_count', label: '外勤次数(次)' },
      { key: 'outside_hours', label: '外出(小时)' },
    ],
  },
]

const dailyFixedColumns = [
  { key: 'date_label', label: '时间' },
  { key: 'employee_name', label: '姓名' },
  { key: 'account', label: '账号' },
]

const dailyDetailColumns = [
  { key: 'date_label', label: '日期' },
  { key: 'employee_name', label: '姓名' },
  { key: 'account', label: '账号' },
  { key: 'department_name', label: '部门' },
  { key: 'position', label: '职务' },
  { key: 'rule_name', label: '所属规则' },
  { key: 'clock_type', label: '打卡类型' },
  { key: 'expected_clock_time', label: '应打卡时间' },
  { key: 'actual_clock_time', label: '实际打卡时间' },
  { key: 'clock_status', label: '打卡状态' },
  { key: 'clock_place', label: '打卡地点' },
  { key: 'clock_device', label: '打卡设备' },
  { key: 'remark_content', label: '备注内容' },
  { key: 'remark_image', label: '备注图片' },
  { key: 'leave_application', label: '假勤申请' },
]

const defaultDailyOverviewVisibleCols = [
  ...dailyFixedColumns.map((item) => item.key),
  ...dailyOverviewColumnGroups.flatMap((group) => group.columns.map((item) => item.key)),
]
const defaultDailyDetailVisibleCols = dailyDetailColumns.map((item) => item.key)

const dailyOverviewVisibleCols = ref<string[]>([...defaultDailyOverviewVisibleCols])
const dailyDetailVisibleCols = ref<string[]>([...defaultDailyDetailVisibleCols])
const dailyOverviewColumnOrder = ref<string[]>([...defaultDailyOverviewVisibleCols])
const dailyDetailColumnOrder = ref<string[]>([...defaultDailyDetailVisibleCols])

const dailyOverviewGroupMap = Object.fromEntries(
  dailyOverviewColumnGroups.map((group) => [group.key, group.columns.map((item) => item.key)]),
)

function normalizeOverviewVisibleCols(cols: string[]) {
  const fixed = dailyFixedColumns.map((item) => item.key)
  const tail = cols.filter((key) => defaultDailyOverviewVisibleCols.includes(key) && !fixed.includes(key))
  return [...fixed, ...tail]
}

function showDailyOverviewCol(key: string) {
  return dailyOverviewVisibleCols.value.includes(key)
}

function showDailyOverviewGroup(groupKey: string) {
  const keys = dailyOverviewGroupMap[groupKey] || []
  return keys.some((key: string) => dailyOverviewVisibleCols.value.includes(key))
}

function showDailyDetailCol(key: string) {
  return dailyDetailVisibleCols.value.includes(key)
}

function loadDailyReportSettings() {
  try {
    const raw = localStorage.getItem(DAILY_REPORT_SETTINGS_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    const overviewOrder = Array.isArray(parsed?.overviewOrder)
      ? normalizeReportOrder(parsed.overviewOrder as string[], defaultDailyOverviewVisibleCols)
      : normalizeReportOrder(parsed?.overview || [], defaultDailyOverviewVisibleCols)
    const detailOrder = Array.isArray(parsed?.detailOrder)
      ? normalizeReportOrder(parsed.detailOrder as string[], defaultDailyDetailVisibleCols)
      : normalizeReportOrder(parsed?.detail || [], defaultDailyDetailVisibleCols)
    const overview = Array.isArray(parsed?.overview) ? normalizeOverviewVisibleCols(parsed.overview as string[]) : []
    const detail = Array.isArray(parsed?.detail) ? parsed.detail.filter((k: string) => defaultDailyDetailVisibleCols.includes(k)) : []
    dailyOverviewColumnOrder.value = overviewOrder
    dailyDetailColumnOrder.value = detailOrder
    if (overview.length) dailyOverviewVisibleCols.value = normalizeOverviewVisibleCols(normalizeVisibleByOrder(overview, overviewOrder, defaultDailyOverviewVisibleCols))
    if (detail.length) dailyDetailVisibleCols.value = normalizeVisibleByOrder(detail, detailOrder, defaultDailyDetailVisibleCols)
  } catch {}
}

function persistDailyReportSettings() {
  localStorage.setItem(
    DAILY_REPORT_SETTINGS_KEY,
    JSON.stringify({
      overview: normalizeOverviewVisibleCols(dailyOverviewVisibleCols.value),
      detail: normalizeVisibleByOrder(dailyDetailVisibleCols.value, dailyDetailColumnOrder.value, defaultDailyDetailVisibleCols),
      overviewOrder: normalizeReportOrder(dailyOverviewColumnOrder.value, defaultDailyOverviewVisibleCols),
      detailOrder: normalizeReportOrder(dailyDetailColumnOrder.value, defaultDailyDetailVisibleCols),
    }),
  )
}

function buildDailyReportQuery(page = dailyPage.page, pageSize = dailyPage.page_size) {
  const params: any = { page, page_size: pageSize, include_recent_left: dailyIncludeRecentLeft.value }
  if (dailyDateRange.value?.length === 2) {
    params.start_date = dailyDateRange.value[0]
    params.end_date = dailyDateRange.value[1]
  }
  if (dailyDept.value) params.department_id = dailyDept.value
  if (dailyKeyword.value.trim()) params.keyword = dailyKeyword.value.trim()
  if (dailyStatus.value) params.status = dailyStatus.value
  if (dailyRuleId.value) params.rule_id = dailyRuleId.value
  return params
}

function normalizeReportCell(value: any) {
  if (value === null || value === undefined) return '-'
  if (typeof value !== 'string') return value
  const cleaned = value.trim()
  if (!cleaned || cleaned === '--') return '-'
  return cleaned.replace(/--/g, '-')
}

function normalizeReportRows(rows: any[]) {
  return (rows || []).map((row) => {
    const normalized: Record<string, any> = {}
    Object.entries(row || {}).forEach(([key, value]) => {
      normalized[key] = normalizeReportCell(value)
    })
    return normalized
  })
}

function reportHeader(label: string) {
  return String(label || '').replace(/([（(][^）)]*[）)])$/, '\n$1')
}

async function fetchDailyReport() {
  dailyLoading.value = true
  try {
    if (!rules.value.length) await fetchRules()
    const res = await get('/attendance/daily-report', buildDailyReportQuery())
    dailyOverviewRows.value = normalizeReportRows(res.overview_rows || [])
    dailyDetailRows.value = normalizeReportRows(res.detail_rows || [])
    dailyPage.total = Number(res.total || 0)
  } catch {
    dailyOverviewRows.value = []
    dailyDetailRows.value = []
    dailyPage.total = 0
    ElMessage.error('日报汇总加载失败')
  } finally {
    dailyLoading.value = false
  }
}

function resetDailyReportFilters() {
  dailyDateRange.value = []
  dailyDept.value = null
  dailyKeyword.value = ''
  dailyStatus.value = ''
  dailyRuleId.value = null
  dailyIncludeRecentLeft.value = true
  dailyPage.page = 1
  fetchDailyReport()
}

function openDailyReportSettings() {
  dailyReportSettingVisible.value = true
}

function resetDailyReportSettings() {
  dailyOverviewColumnOrder.value = [...defaultDailyOverviewVisibleCols]
  dailyDetailColumnOrder.value = [...defaultDailyDetailVisibleCols]
  dailyOverviewVisibleCols.value = [...defaultDailyOverviewVisibleCols]
  dailyDetailVisibleCols.value = [...defaultDailyDetailVisibleCols]
}

function saveDailyReportSettings() {
  dailyOverviewColumnOrder.value = normalizeReportOrder(dailyOverviewColumnOrder.value, defaultDailyOverviewVisibleCols)
  dailyDetailColumnOrder.value = normalizeReportOrder(dailyDetailColumnOrder.value, defaultDailyDetailVisibleCols)
  dailyOverviewVisibleCols.value = normalizeOverviewVisibleCols(
    normalizeVisibleByOrder(dailyOverviewVisibleCols.value, dailyOverviewColumnOrder.value, defaultDailyOverviewVisibleCols),
  )
  dailyDetailVisibleCols.value = normalizeVisibleByOrder(dailyDetailVisibleCols.value, dailyDetailColumnOrder.value, defaultDailyDetailVisibleCols)
  if (!dailyOverviewVisibleCols.value.length) {
    ElMessage.warning('日报概况至少保留 1 列')
    return
  }
  if (!dailyDetailVisibleCols.value.length) {
    ElMessage.warning('日报明细至少保留 1 列')
    return
  }
  persistDailyReportSettings()
  dailyReportSettingVisible.value = false
  ElMessage.success('报表设置已保存')
}

function visibleOverviewColsForExport() {
  return normalizeVisibleByOrder(dailyOverviewVisibleCols.value, dailyOverviewColumnOrder.value, defaultDailyOverviewVisibleCols).join(',')
}

function visibleDetailColsForExport() {
  return normalizeVisibleByOrder(dailyDetailVisibleCols.value, dailyDetailColumnOrder.value, defaultDailyDetailVisibleCols).join(',')
}

function downloadBlobFile(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)
}

async function exportDailyReport() {
  const query = buildDailyReportQuery(1, 1000)
  delete query.page
  delete query.page_size
  query.overview_columns = visibleOverviewColsForExport()
  query.detail_columns = visibleDetailColsForExport()
  try {
    const blob = await get<Blob>('/attendance/daily-report/export', query, { responseType: 'blob' })
    downloadBlobFile(blob, `attendance_daily_${new Date().toISOString().slice(0, 10)}.xlsx`)
  } catch {
    ElMessage.error('导出日报失败')
  }
}

// ============ 月度汇总 ============
const summarySubTab = ref<'overview' | 'detail'>('overview')
const summaryOverviewRows = ref<any[]>([])
const summaryDetailRows = ref<any[]>([])
const summaryDayColumns = ref<Array<{ key: string; title: string; date?: string }>>([])
const summaryLoading = ref(false)
const summaryTaskCreating = ref(false)
const summaryFetchSeq = ref(0)
const summaryDateRange = ref<string[]>(defaultSummaryDateRange())
const summaryDept = ref<number | null>(null)
const summaryKeyword = ref('')
const summaryStatus = ref('')
const summaryRuleId = ref<number | null>(null)
const summaryIncludeRecentLeft = ref(true)
const summaryPage = reactive({ page: 1, page_size: 20, total: 0 })
type SummaryTaskStatus = 'idle' | 'pending' | 'running' | 'succeeded' | 'failed'
const summaryTaskId = ref('')
const summaryTaskStatus = ref<SummaryTaskStatus>('idle')
const summaryTaskProgress = ref(0)
const summaryTaskMessage = ref('')
const summaryTaskError = ref('')
const summaryTaskFromCache = ref(false)
const summaryTaskPollTimer = ref<number | null>(null)
const summaryTaskPolling = ref(false)
const summaryReportSettingVisible = ref(false)
const summarySettingTab = ref<'overview' | 'detail'>('overview')
const summarySettingOpenGroups = ref<string[]>(['fixed', 'basic', 'attendance', 'abnormal', 'leave', 'overtime'])
const SUMMARY_REPORT_SETTINGS_KEY = 'attendanceMonthlyReportSettingsV3'
const SUMMARY_TASK_STORAGE_KEY = 'attendanceMonthlyReportTaskV1'

const summaryOverviewColumnGroups = [
  {
    key: 'fixed',
    label: '固定信息',
    columns: [
      { key: 'employee_name', label: '姓名' },
      { key: 'account', label: '账号' },
      { key: 'rule_name', label: '所属规则' },
    ],
  },
  {
    key: 'basic',
    label: '基础信息',
    columns: [
      { key: 'department_name', label: '部门' },
      { key: 'position', label: '职务' },
    ],
  },
  {
    key: 'attendance',
    label: '考勤概况',
    columns: [
      { key: 'expected_days', label: '应出勤天数' },
      { key: 'actual_days', label: '实际出勤天数' },
      { key: 'rest_days', label: '休息天数' },
      { key: 'normal_days', label: '正常天数' },
      { key: 'abnormal_days', label: '异常天数' },
      { key: 'standard_work_hours', label: '标准工作时长' },
      { key: 'actual_work_hours', label: '实际工作时长' },
    ],
  },
  {
    key: 'abnormal',
    label: '异常统计',
    columns: [
      { key: 'anomaly_total', label: '异常合计' },
      { key: 'late_count', label: '迟到次数' },
      { key: 'late_minutes', label: '迟到时长' },
      { key: 'early_count', label: '早退次数' },
      { key: 'early_minutes', label: '早退时长' },
      { key: 'absent_count', label: '旷工次数' },
      { key: 'absent_minutes', label: '旷工时长' },
      { key: 'missed_count', label: '缺卡次数' },
      { key: 'location_abnormal_count', label: '地点异常' },
      { key: 'device_abnormal_count', label: '设备异常' },
    ],
  },
  {
    key: 'leave',
    label: '假勤统计',
    columns: [
      { key: 'punch_correction_count', label: '补卡次数' },
      { key: 'approve_punch_count', label: '审批打卡次数' },
      { key: 'field_work_count', label: '外勤次数' },
      { key: 'outside_hours', label: '外出' },
      { key: 'business_trip_days', label: '出差' },
      { key: 'annual_leave_days', label: '年假' },
      { key: 'marriage_leave_days', label: '婚假' },
      { key: 'maternity_leave_hours', label: '产休假' },
      { key: 'paternity_leave_days', label: '陪产假' },
      { key: 'sick_leave_days', label: '病假' },
      { key: 'lieu_leave_hours', label: '调休' },
      { key: 'personal_leave_days', label: '事假' },
      { key: 'long_sick_leave_days', label: '病假（长期）' },
      { key: 'prenatal_leave_days', label: '产检假' },
      { key: 'bereavement_leave_days', label: '丧假' },
      { key: 'general_sick_leave_days', label: '病假（通用）' },
    ],
  },
  {
    key: 'overtime',
    label: '加班统计',
    columns: [
      { key: 'overtime_hours', label: '加班时长' },
      { key: 'weekday_ot_hours', label: '工作日加班时长' },
      { key: 'weekday_ot_to_rest', label: '工作日加班计为调休' },
      { key: 'weekday_ot_to_pay', label: '工作日加班计为加班费' },
      { key: 'weekend_ot_hours', label: '休息日加班时长' },
      { key: 'weekend_ot_to_rest', label: '休息日加班计为调休' },
      { key: 'weekend_ot_to_pay', label: '休息日加班计为加班费' },
      { key: 'holiday_ot_hours', label: '节假日加班时长' },
      { key: 'holiday_ot_to_rest', label: '节假日加班计为调休' },
      { key: 'holiday_ot_to_pay', label: '节假日加班计为加班费' },
    ],
  },
]

const summaryDetailBaseColumns = [
  { key: 'employee_name', label: '姓名' },
  { key: 'account', label: '账号' },
  { key: 'rule_name', label: '所属规则' },
  { key: 'department_name', label: '部门' },
  { key: 'position', label: '职务' },
]

const defaultSummaryOverviewVisibleCols = [
  ...summaryOverviewColumnGroups.flatMap((group) => group.columns.map((item) => item.key)),
]
const defaultSummaryDetailVisibleCols = summaryDetailBaseColumns.map((item) => item.key)
const summaryOverviewVisibleCols = ref<string[]>([...defaultSummaryOverviewVisibleCols])
const summaryDetailVisibleCols = ref<string[]>([...defaultSummaryDetailVisibleCols])
const summaryOverviewColumnOrder = ref<string[]>([...defaultSummaryOverviewVisibleCols])
const summaryDetailColumnOrder = ref<string[]>([...defaultSummaryDetailVisibleCols])
const summaryOverviewGroupMap = Object.fromEntries(
  summaryOverviewColumnGroups.map((group) => [group.key, group.columns.map((item) => item.key)]),
)
const summaryConfirmText = computed(() => `查看考勤确认情况(0/${summaryPage.total || 0})`)
const summaryTaskVisible = computed(() => summaryTaskStatus.value !== 'idle' || !!summaryTaskId.value)
const canExportSummary = computed(() => summaryTaskStatus.value === 'succeeded' && !!summaryTaskId.value && summaryPage.total >= 0)
const summaryTaskStatusText = computed(() => {
  const map: Record<SummaryTaskStatus, string> = {
    idle: '未生成',
    pending: '排队中',
    running: '生成中',
    succeeded: '已完成',
    failed: '生成失败',
  }
  return map[summaryTaskStatus.value] || '未生成'
})
const summaryTaskTagType = computed(() => {
  if (summaryTaskStatus.value === 'succeeded') return 'success'
  if (summaryTaskStatus.value === 'failed') return 'danger'
  if (summaryTaskStatus.value === 'running' || summaryTaskStatus.value === 'pending') return 'warning'
  return 'info'
})
const summaryTaskDisplayMessage = computed(() => {
  if (summaryTaskStatus.value === 'failed') return summaryTaskError.value || summaryTaskMessage.value || '月报生成失败'
  if (summaryTaskStatus.value === 'succeeded') return summaryTaskMessage.value || `已生成 ${summaryPage.total || 0} 条月报数据`
  return summaryTaskMessage.value || '月报任务已提交'
})

type ReportSettingKind = 'dailyOverview' | 'dailyDetail' | 'summaryOverview' | 'summaryDetail'
type ReportColumnItem = { key: string; label: string }
type ReportColumnGroup = { key: string; label: string; columns: ReportColumnItem[] }
type ReportDropPosition = 'before' | 'after'

const reportSettingDrag = ref<{ kind: ReportSettingKind; key: string } | null>(null)
const reportSettingDropTarget = ref<{ kind: ReportSettingKind; key: string; position: ReportDropPosition } | null>(null)

const reportColumnWidthMap: Record<string, number> = {
  date_label: 160,
  employee_name: 130,
  account: 120,
  department_name: 220,
  position: 120,
  rule_name: 170,
  shift_name: 150,
  earliest_clock: 100,
  latest_clock: 100,
  clock_count: 120,
  standard_work_hours: 150,
  actual_work_hours: 150,
  leave_application: 180,
  attendance_result: 140,
  anomaly_total: 120,
  late_count: 120,
  late_minutes: 130,
  early_count: 120,
  early_minutes: 130,
  absent_count: 120,
  absent_minutes: 130,
  missed_count: 120,
  location_abnormal_count: 120,
  device_abnormal_count: 120,
  punch_correction_count: 120,
  outside_clock_count: 140,
  outside_earliest: 100,
  outside_latest: 100,
  overtime_status: 120,
  overtime_hours: 130,
  weekday_ot_to_rest: 180,
  weekday_ot_to_pay: 200,
  weekend_ot_to_rest: 180,
  weekend_ot_to_pay: 200,
  holiday_ot_to_rest: 190,
  holiday_ot_to_pay: 210,
  approve_punch_count: 140,
  field_work_count: 120,
  outside_hours: 110,
  clock_type: 110,
  expected_clock_time: 130,
  actual_clock_time: 130,
  clock_status: 180,
  clock_place: 180,
  clock_device: 180,
  remark_content: 200,
  remark_image: 180,
  expected_days: 120,
  actual_days: 130,
  rest_days: 110,
  normal_days: 110,
  abnormal_days: 110,
  business_trip_days: 100,
  annual_leave_days: 100,
  marriage_leave_days: 100,
  maternity_leave_hours: 110,
  paternity_leave_days: 110,
  sick_leave_days: 100,
  lieu_leave_hours: 100,
  personal_leave_days: 100,
  long_sick_leave_days: 130,
  prenatal_leave_days: 110,
  bereavement_leave_days: 100,
  general_sick_leave_days: 130,
  weekday_ot_hours: 150,
  weekend_ot_hours: 150,
  holiday_ot_hours: 160,
}

const reportOverflowColumns = new Set([
  'department_name',
  'position',
  'rule_name',
  'leave_application',
  'attendance_result',
  'clock_status',
  'clock_place',
  'clock_device',
  'remark_content',
  'remark_image',
])

function reportVisibleRef(kind: ReportSettingKind) {
  const refs = {
    dailyOverview: dailyOverviewVisibleCols,
    dailyDetail: dailyDetailVisibleCols,
    summaryOverview: summaryOverviewVisibleCols,
    summaryDetail: summaryDetailVisibleCols,
  }
  return refs[kind]
}

function reportOrderRef(kind: ReportSettingKind) {
  const refs = {
    dailyOverview: dailyOverviewColumnOrder,
    dailyDetail: dailyDetailColumnOrder,
    summaryOverview: summaryOverviewColumnOrder,
    summaryDetail: summaryDetailColumnOrder,
  }
  return refs[kind]
}

function reportDefaultKeys(kind: ReportSettingKind) {
  const maps = {
    dailyOverview: defaultDailyOverviewVisibleCols,
    dailyDetail: defaultDailyDetailVisibleCols,
    summaryOverview: defaultSummaryOverviewVisibleCols,
    summaryDetail: defaultSummaryDetailVisibleCols,
  }
  return maps[kind]
}

function normalizeReportOrder(order: string[], defaults: string[]) {
  const seen = new Set<string>()
  const next = (order || []).filter((key) => defaults.includes(key) && !seen.has(key) && seen.add(key))
  defaults.forEach((key) => {
    if (!seen.has(key)) next.push(key)
  })
  return next
}

function normalizeVisibleByOrder(visible: string[], order: string[], defaults: string[]) {
  const selected = new Set((visible || []).filter((key) => defaults.includes(key)))
  return normalizeReportOrder(order, defaults).filter((key) => selected.has(key))
}

function orderedReportSettingColumns(kind: ReportSettingKind, columns: ReportColumnItem[]) {
  const order = reportOrderRef(kind).value
  const indexMap = new Map(order.map((key, index) => [key, index]))
  return [...columns].sort((a, b) => (indexMap.get(a.key) ?? 9999) - (indexMap.get(b.key) ?? 9999))
}

function visibleReportSettingColumns(kind: ReportSettingKind, columns: ReportColumnItem[]) {
  const selected = new Set(reportVisibleRef(kind).value)
  return orderedReportSettingColumns(kind, columns).filter((col) => selected.has(col.key))
}

function reportGroupColumns(groups: ReportColumnGroup[], groupKey: string) {
  return groups.find((group) => group.key === groupKey)?.columns || []
}

function visibleReportGroupColumns(kind: ReportSettingKind, groups: ReportColumnGroup[], groupKey: string) {
  return visibleReportSettingColumns(kind, reportGroupColumns(groups, groupKey))
}

function reportColumnLabel(col: ReportColumnItem) {
  return reportHeader(col.label)
}

function reportColumnWidth(key: string) {
  return reportColumnWidthMap[key] || 120
}

function reportColumnOverflow(key: string) {
  return reportOverflowColumns.has(key)
}

function reportColumnFixed(kind: ReportSettingKind, key: string) {
  if (kind === 'dailyOverview') return ['date_label', 'employee_name'].includes(key)
  if (kind === 'dailyDetail') return ['date_label', 'employee_name'].includes(key)
  if (kind === 'summaryOverview') return key === 'employee_name'
  if (kind === 'summaryDetail') return key === 'employee_name'
  return false
}

function countVisibleReportColumns(kind: ReportSettingKind, columns: ReportColumnItem[]) {
  const selected = new Set(reportVisibleRef(kind).value)
  return columns.filter((col) => selected.has(col.key)).length
}

function isReportColumnVisible(kind: ReportSettingKind, key: string) {
  return reportVisibleRef(kind).value.includes(key)
}

function toggleReportColumn(kind: ReportSettingKind, key: string, checked: string | number | boolean) {
  const visibleRef = reportVisibleRef(kind)
  const selected = new Set(visibleRef.value)
  if (Boolean(checked)) selected.add(key)
  else selected.delete(key)
  const defaults = reportDefaultKeys(kind)
  visibleRef.value = normalizeVisibleByOrder([...selected], reportOrderRef(kind).value, defaults)
}

function startReportColumnDrag(kind: ReportSettingKind, key: string) {
  reportSettingDrag.value = { kind, key }
  reportSettingDropTarget.value = null
}

function enterReportColumnDrop(kind: ReportSettingKind, key: string, event?: DragEvent) {
  const drag = reportSettingDrag.value
  if (!drag || drag.kind !== kind || drag.key === key) {
    reportSettingDropTarget.value = null
    return
  }
  let position: ReportDropPosition = 'before'
  const target = event?.currentTarget as HTMLElement | null
  if (target && typeof event?.clientY === 'number') {
    const rect = target.getBoundingClientRect()
    position = event.clientY > rect.top + rect.height / 2 ? 'after' : 'before'
  }
  reportSettingDropTarget.value = { kind, key, position }
}

function clearReportColumnDrag() {
  reportSettingDrag.value = null
  reportSettingDropTarget.value = null
}

function reportFieldRowClass(kind: ReportSettingKind, key: string) {
  const drag = reportSettingDrag.value
  const dropTarget = reportSettingDropTarget.value
  return {
    'report-field-row': true,
    'is-dragging': drag?.kind === kind && drag.key === key,
    'is-drop-target': dropTarget?.kind === kind && dropTarget.key === key,
    'is-drop-before': dropTarget?.kind === kind && dropTarget.key === key && dropTarget.position === 'before',
    'is-drop-after': dropTarget?.kind === kind && dropTarget.key === key && dropTarget.position === 'after',
  }
}

function dropReportColumn(kind: ReportSettingKind, targetKey: string) {
  const drag = reportSettingDrag.value
  const dropTarget = reportSettingDropTarget.value
  clearReportColumnDrag()
  if (!drag || drag.kind !== kind || drag.key === targetKey) return
  const orderRef = reportOrderRef(kind)
  const defaults = reportDefaultKeys(kind)
  const order = normalizeReportOrder(orderRef.value, defaults)
  const from = order.indexOf(drag.key)
  let to = order.indexOf(targetKey)
  if (from < 0 || to < 0) return
  if (dropTarget?.kind === kind && dropTarget.key === targetKey && dropTarget.position === 'after') {
    to += 1
  }
  const [moved] = order.splice(from, 1)
  if (from < to) to -= 1
  order.splice(to, 0, moved)
  orderRef.value = normalizeReportOrder(order, defaults)
  const visibleRef = reportVisibleRef(kind)
  visibleRef.value = normalizeVisibleByOrder(visibleRef.value, orderRef.value, defaults)
}

function formatLocalDate(d: Date) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function defaultSummaryDateRange() {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  return [formatLocalDate(start), formatLocalDate(now)]
}

function normalizeSummaryOverviewVisibleCols(cols: string[]) {
  return cols.filter((key) => defaultSummaryOverviewVisibleCols.includes(key))
}

function normalizeSummaryDetailVisibleCols(cols: string[]) {
  return cols.filter((key) => defaultSummaryDetailVisibleCols.includes(key))
}

function showSummaryOverviewCol(key: string) {
  return summaryOverviewVisibleCols.value.includes(key)
}

function showSummaryOverviewGroup(groupKey: string) {
  const keys = summaryOverviewGroupMap[groupKey] || []
  return keys.some((key: string) => summaryOverviewVisibleCols.value.includes(key))
}

function showSummaryDetailCol(key: string) {
  return summaryDetailVisibleCols.value.includes(key)
}

function loadSummaryReportSettings() {
  try {
    const raw = localStorage.getItem(SUMMARY_REPORT_SETTINGS_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    const overviewOrder = Array.isArray(parsed?.overviewOrder)
      ? normalizeReportOrder(parsed.overviewOrder as string[], defaultSummaryOverviewVisibleCols)
      : normalizeReportOrder(parsed?.overview || [], defaultSummaryOverviewVisibleCols)
    const detailOrder = Array.isArray(parsed?.detailOrder)
      ? normalizeReportOrder(parsed.detailOrder as string[], defaultSummaryDetailVisibleCols)
      : normalizeReportOrder(parsed?.detail || [], defaultSummaryDetailVisibleCols)
    const overview = Array.isArray(parsed?.overview) ? normalizeSummaryOverviewVisibleCols(parsed.overview as string[]) : []
    const detail = Array.isArray(parsed?.detail) ? normalizeSummaryDetailVisibleCols(parsed.detail as string[]) : []
    summaryOverviewColumnOrder.value = overviewOrder
    summaryDetailColumnOrder.value = detailOrder
    if (overview.length) summaryOverviewVisibleCols.value = normalizeVisibleByOrder(overview, overviewOrder, defaultSummaryOverviewVisibleCols)
    if (detail.length) summaryDetailVisibleCols.value = normalizeVisibleByOrder(detail, detailOrder, defaultSummaryDetailVisibleCols)
  } catch {}
}

function persistSummaryReportSettings() {
  localStorage.setItem(
    SUMMARY_REPORT_SETTINGS_KEY,
    JSON.stringify({
      overview: normalizeVisibleByOrder(summaryOverviewVisibleCols.value, summaryOverviewColumnOrder.value, defaultSummaryOverviewVisibleCols),
      detail: normalizeVisibleByOrder(summaryDetailVisibleCols.value, summaryDetailColumnOrder.value, defaultSummaryDetailVisibleCols),
      overviewOrder: normalizeReportOrder(summaryOverviewColumnOrder.value, defaultSummaryOverviewVisibleCols),
      detailOrder: normalizeReportOrder(summaryDetailColumnOrder.value, defaultSummaryDetailVisibleCols),
    }),
  )
}

function buildSummaryReportQuery(page = summaryPage.page, pageSize = summaryPage.page_size) {
  if (!summaryDateRange.value?.length) {
    summaryDateRange.value = defaultSummaryDateRange()
  }
  const params: any = { page, page_size: pageSize, include_recent_left: summaryIncludeRecentLeft.value }
  if (summaryDateRange.value?.length === 2) {
    params.start_date = summaryDateRange.value[0]
    params.end_date = summaryDateRange.value[1]
  }
  if (summaryDept.value) params.department_id = summaryDept.value
  if (summaryKeyword.value.trim()) params.keyword = summaryKeyword.value.trim()
  if (summaryStatus.value) params.status = summaryStatus.value
  if (summaryRuleId.value) params.rule_id = summaryRuleId.value
  return params
}

function buildSummaryReportTaskPayload(forceRegenerate = false) {
  const params = buildSummaryReportQuery(1, summaryPage.page_size)
  delete params.page
  delete params.page_size
  return {
    ...params,
    force_regenerate: forceRegenerate,
  }
}

function requestErrorText(error: any, fallback: string) {
  return error?.response?.data?.detail || error?.message || fallback
}

function applySummaryTaskQuery(query: any) {
  if (!query || typeof query !== 'object') return
  if (query.start_date && query.end_date) summaryDateRange.value = [query.start_date, query.end_date]
  summaryDept.value = query.department_id ?? null
  summaryKeyword.value = query.keyword || ''
  summaryStatus.value = query.status || ''
  summaryRuleId.value = query.rule_id ?? null
  summaryIncludeRecentLeft.value = query.include_recent_left !== false
}

function persistSummaryTask(task: any) {
  if (!task?.task_id) return
  localStorage.setItem(
    SUMMARY_TASK_STORAGE_KEY,
    JSON.stringify({
      task_id: task.task_id,
      query: task.query || null,
      status: task.status || null,
      updated_at: task.updated_at || null,
    }),
  )
}

function applySummaryTask(task: any, options: { restoreQuery?: boolean } = {}) {
  summaryTaskId.value = task?.task_id || ''
  summaryTaskStatus.value = (task?.status || 'idle') as SummaryTaskStatus
  summaryTaskProgress.value = Number(task?.progress || 0)
  summaryTaskMessage.value = task?.message || ''
  summaryTaskError.value = task?.error || ''
  summaryTaskFromCache.value = Boolean(task?.from_cache)
  if (Number.isFinite(Number(task?.row_count))) summaryPage.total = Number(task.row_count || summaryPage.total || 0)
  if (options.restoreQuery) applySummaryTaskQuery(task?.query)
  persistSummaryTask(task)
}

function stopSummaryTaskPolling() {
  if (summaryTaskPollTimer.value) {
    window.clearInterval(summaryTaskPollTimer.value)
    summaryTaskPollTimer.value = null
  }
  summaryTaskPolling.value = false
}

async function loadSummaryTaskResult(taskId = summaryTaskId.value) {
  if (!taskId) return
  summaryLoading.value = true
  try {
    const res = await get(`/attendance/monthly-report/tasks/${taskId}/result`, {
      page: summaryPage.page,
      page_size: summaryPage.page_size,
    })
    summaryOverviewRows.value = normalizeReportRows(res.overview_rows || [])
    summaryDetailRows.value = normalizeReportRows(res.detail_rows || [])
    summaryDayColumns.value = res.detail_day_columns || []
    summaryPage.total = Number(res.total || 0)
    summaryTaskStatus.value = 'succeeded'
    summaryTaskMessage.value = `已生成 ${summaryPage.total || 0} 条月报数据`
  } catch (error: any) {
    ElMessage.error(requestErrorText(error, '月报结果加载失败'))
  } finally {
    summaryLoading.value = false
  }
}

async function pollSummaryTask(taskId: string, requestId: number) {
  if (summaryTaskPolling.value) return
  summaryTaskPolling.value = true
  try {
    const task = await get(`/attendance/monthly-report/tasks/${taskId}`)
    if (requestId !== summaryFetchSeq.value) return
    applySummaryTask(task)
    if (task.status === 'succeeded') {
      stopSummaryTaskPolling()
      await loadSummaryTaskResult(taskId)
    } else if (task.status === 'failed') {
      stopSummaryTaskPolling()
      ElMessage.error(task.error || '月报生成失败')
    }
  } catch (error: any) {
    if (requestId !== summaryFetchSeq.value) return
    stopSummaryTaskPolling()
    summaryTaskStatus.value = 'failed'
    summaryTaskError.value = requestErrorText(error, '月报任务状态获取失败')
  } finally {
    summaryTaskPolling.value = false
  }
}

function startSummaryTaskPolling(taskId: string, requestId: number) {
  stopSummaryTaskPolling()
  summaryTaskPollTimer.value = window.setInterval(() => {
    void pollSummaryTask(taskId, requestId)
  }, 1500)
}

async function fetchSummary(forceRegenerate: boolean | Event | number = false) {
  const shouldForce = forceRegenerate === true
  const requestId = ++summaryFetchSeq.value
  summaryTaskCreating.value = true
  summaryLoading.value = true
  stopSummaryTaskPolling()
  try {
    // 规则下拉仅用于筛选，不应阻塞月报任务创建。
    if (!rules.value.length) void fetchRules()
    const task = await post('/attendance/monthly-report/tasks', buildSummaryReportTaskPayload(shouldForce), { timeout: 5000 })
    if (requestId !== summaryFetchSeq.value) return
    applySummaryTask(task)
    if (task.status === 'succeeded') {
      await loadSummaryTaskResult(task.task_id)
    } else if (task.status === 'failed') {
      summaryLoading.value = false
      ElMessage.error(task.error || '月报生成失败')
    } else {
      summaryLoading.value = false
      startSummaryTaskPolling(task.task_id, requestId)
    }
  } catch (error: any) {
    if (requestId !== summaryFetchSeq.value) return
    summaryTaskStatus.value = 'failed'
    summaryTaskError.value = requestErrorText(error, '月报任务创建失败')
    ElMessage.error(summaryTaskError.value)
    summaryLoading.value = false
  } finally {
    if (requestId === summaryFetchSeq.value) summaryTaskCreating.value = false
  }
}

function handleSummaryPageChange() {
  if (summaryTaskId.value && summaryTaskStatus.value === 'succeeded') {
    loadSummaryTaskResult()
    return
  }
  fetchSummary()
}

function resetSummaryReportFilters() {
  summaryDateRange.value = defaultSummaryDateRange()
  summaryDept.value = null
  summaryKeyword.value = ''
  summaryStatus.value = ''
  summaryRuleId.value = null
  summaryIncludeRecentLeft.value = true
  summaryPage.page = 1
  fetchSummary()
}

function openSummaryReportSettings() {
  summaryReportSettingVisible.value = true
}

function resetSummaryReportSettings() {
  summaryOverviewColumnOrder.value = [...defaultSummaryOverviewVisibleCols]
  summaryDetailColumnOrder.value = [...defaultSummaryDetailVisibleCols]
  summaryOverviewVisibleCols.value = [...defaultSummaryOverviewVisibleCols]
  summaryDetailVisibleCols.value = [...defaultSummaryDetailVisibleCols]
}

function saveSummaryReportSettings() {
  summaryOverviewColumnOrder.value = normalizeReportOrder(summaryOverviewColumnOrder.value, defaultSummaryOverviewVisibleCols)
  summaryDetailColumnOrder.value = normalizeReportOrder(summaryDetailColumnOrder.value, defaultSummaryDetailVisibleCols)
  summaryOverviewVisibleCols.value = normalizeVisibleByOrder(summaryOverviewVisibleCols.value, summaryOverviewColumnOrder.value, defaultSummaryOverviewVisibleCols)
  summaryDetailVisibleCols.value = normalizeVisibleByOrder(summaryDetailVisibleCols.value, summaryDetailColumnOrder.value, defaultSummaryDetailVisibleCols)
  if (!summaryOverviewVisibleCols.value.length) {
    ElMessage.warning('月报概况至少保留 1 列')
    return
  }
  if (!summaryDetailVisibleCols.value.length) {
    ElMessage.warning('月报明细至少保留 1 列基础信息')
    return
  }
  persistSummaryReportSettings()
  summaryReportSettingVisible.value = false
  ElMessage.success('报表设置已保存')
}

async function exportSummary() {
  if (!summaryTaskId.value || summaryTaskStatus.value !== 'succeeded') {
    ElMessage.warning('请先生成月报')
    return
  }
  const query: any = {
    overview_columns: normalizeVisibleByOrder(summaryOverviewVisibleCols.value, summaryOverviewColumnOrder.value, defaultSummaryOverviewVisibleCols).join(','),
    detail_columns: normalizeVisibleByOrder(summaryDetailVisibleCols.value, summaryDetailColumnOrder.value, defaultSummaryDetailVisibleCols).join(','),
  }
  try {
    const blob = await get<Blob>(`/attendance/monthly-report/tasks/${summaryTaskId.value}/export`, query, { responseType: 'blob', timeout: 120000 })
    const start = summaryDateRange.value?.[0] || new Date().toISOString().slice(0, 10)
    const end = summaryDateRange.value?.[1] || start
    downloadBlobFile(blob, `上下班打卡_月报_${start.replaceAll('-', '')}-${end.replaceAll('-', '')}.xlsx`)
  } catch (error: any) {
    ElMessage.error(requestErrorText(error, '导出月报失败'))
  }
}

// ============ 打卡时间记录 ============
type PunchTimeDateColumn = { date: string; day: string; weekday: string; title?: string }

function defaultPunchTimeDateRange() {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  return [formatLocalDate(start), formatLocalDate(now)]
}

const punchTimeDateRange = ref<string[]>(defaultPunchTimeDateRange())
const punchTimeDept = ref<number | null>(null)
const punchTimeRuleId = ref<number | null>(null)
const punchTimeKeyword = ref('')
const punchTimeIncludeRecentLeft = ref(true)
const punchTimeLoading = ref(false)
const punchTimeRows = ref<any[]>([])
const punchTimeDateColumns = ref<PunchTimeDateColumn[]>([])
const punchTimePage = reactive({ page: 1, page_size: 20, total: 0 })

function punchTimeDayHeader(col: PunchTimeDateColumn) {
  return `${col.day}\n${col.weekday}`
}

function buildPunchTimeQuery(page = punchTimePage.page, pageSize = punchTimePage.page_size) {
  if (!punchTimeDateRange.value?.length) {
    punchTimeDateRange.value = defaultPunchTimeDateRange()
  }
  const params: any = {
    page,
    page_size: pageSize,
    include_recent_left: punchTimeIncludeRecentLeft.value,
  }
  if (punchTimeDateRange.value?.length === 2) {
    params.start_date = punchTimeDateRange.value[0]
    params.end_date = punchTimeDateRange.value[1]
  }
  if (punchTimeDept.value) params.department_id = punchTimeDept.value
  if (punchTimeRuleId.value) params.rule_id = punchTimeRuleId.value
  if (punchTimeKeyword.value.trim()) params.keyword = punchTimeKeyword.value.trim()
  return params
}

async function fetchPunchTimeRecords() {
  punchTimeLoading.value = true
  try {
    const res = await get('/attendance/punch-time-records', buildPunchTimeQuery())
    punchTimeRows.value = res.items || []
    punchTimeDateColumns.value = res.date_columns || []
    punchTimePage.total = Number(res.total || 0)
  } catch {
    punchTimeRows.value = []
    punchTimeDateColumns.value = []
    punchTimePage.total = 0
    ElMessage.error('打卡时间记录加载失败')
  } finally {
    punchTimeLoading.value = false
  }
}

function resetPunchTimeFilters() {
  punchTimeDateRange.value = defaultPunchTimeDateRange()
  punchTimeDept.value = null
  punchTimeRuleId.value = null
  punchTimeKeyword.value = ''
  punchTimeIncludeRecentLeft.value = true
  punchTimePage.page = 1
  fetchPunchTimeRecords()
}

async function exportPunchTimeRecords() {
  const query = buildPunchTimeQuery(1, 1000)
  delete query.page
  delete query.page_size
  try {
    const blob = await get<Blob>('/attendance/punch-time-records/export', query, { responseType: 'blob' })
    downloadBlobFile(blob, `attendance_punch_time_${new Date().toISOString().slice(0, 10)}.xlsx`)
  } catch {
    ElMessage.error('打卡时间记录导出失败')
  }
}

// ============ Today stats ============
async function fetchTodayStats() {
  try {
    // 优先用后端接口
    const stats = await get('/attendance/today-stats', undefined, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    if (stats && (stats.total || stats.checked_in)) {
      todayStats.total = stats.total || 0
      todayStats.present = stats.checked_in || 0
      todayStats.late = stats.late || 0
      todayStats.leave = stats.on_leave || 0
      todayStats.outside = 0
      return
    }
    // 如果今天没数据，用最近的考勤数据展示
    const res = await get('/attendance/records', { page: 1, page_size: 999 })
    const list: any[] = res.items || res.data || []
    if (list.length > 0) {
      // 获取最近一天的日期
      const latestDate = list[0]?.date
      const dayRecords = list.filter((r: any) => r.date === latestDate)
      todayStats.total = dayRecords.length
      todayStats.present = dayRecords.filter((r: any) => r.clock_in_time && statusParts(normalizeStatus(r)).some((s) => ['正常', '迟到', '早退'].includes(s))).length
      todayStats.late = dayRecords.filter((r: any) => statusParts(normalizeStatus(r)).includes('迟到')).length
      todayStats.leave = dayRecords.filter((r: any) => r.status === '请假').length
      todayStats.outside = dayRecords.filter((r: any) => r.status === '出差').length
    }
  } catch {}
}

// ============ 考勤审批 ============
const anomalyList = ref<any[]>([])
const anomalyLoading = ref(false)
const appealSourceFilter = ref<'all' | 'application' | 'anomaly'>('all')
const appealTypeFilter = ref('')
const appealStatusFilter = ref('')
const approvalTemplateOptions = ref<Array<{ value: string; label: string }>>([])
const appealDetailVisible = ref(false)
const appealDetailLoading = ref(false)
const appealDetail = ref<any>(null)

const appealTypeOptions = computed(() => {
  const options = new Map<string, string>()
  approvalTemplateOptions.value.forEach((item) => options.set(item.value, item.label))
  anomalyList.value.forEach((item: any) => {
    if (item.type_value && item.type_label) options.set(item.type_value, item.type_label)
  })
  return Array.from(options.entries()).map(([value, label]) => ({ value, label }))
})

const filteredAnomalyList = computed(() => {
  return anomalyList.value.filter((item: any) => {
    if (appealSourceFilter.value === 'application' && item.source === 'attendance_anomaly') return false
    if (appealSourceFilter.value === 'anomaly' && item.source !== 'attendance_anomaly') return false
    if (appealTypeFilter.value && item.type_value !== appealTypeFilter.value) return false
    if (appealStatusFilter.value && item.status !== appealStatusFilter.value) return false
    return true
  })
})

const approvalFormRows = computed(() => {
  const detail = appealDetail.value || {}
  const formData = detail.form_data && typeof detail.form_data === 'object' ? detail.form_data : {}
  const labels = detail.form_field_labels && typeof detail.form_field_labels === 'object' ? detail.form_field_labels : {}
  const displayValues = detail.form_field_display_values && typeof detail.form_field_display_values === 'object' ? detail.form_field_display_values : {}
  return Object.keys(formData)
    .filter((key) => !['undefined', 'null'].includes(key))
    .map((key) => ({
      key,
      label: labels[key] || fieldLabelText(key),
      value: fieldDisplayText(displayValues[key] ?? formData[key]),
    }))
    .filter((item) => item.value !== '')
})

const approvalRecordRows = computed(() => {
  const rows = Array.isArray(appealDetail.value?.records) ? appealDetail.value.records : []
  return rows.map((item: any) => ({
    ...item,
    approver_name: item.approver_name || employeeMap.value[item.approver_id]?.name || '',
  }))
})

async function fetchAnomalies() {
  anomalyLoading.value = true
  try {
    const endDate = new Date().toISOString().split('T')[0]
    const startDate = new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString().split('T')[0]
    const [templateRes, approvalRes, attendanceRes]: any[] = await Promise.all([
      get('/approval/templates', { include_inactive: true }).catch(() => []),
      get('/approval/instances', { group: 'submitted', skip: 0, limit: 200 }).catch(() => ({ items: [] })),
      get('/attendance/anomalies', { start_date: startDate, end_date: endDate, page_size: 100 }).catch(() => ({ items: [] })),
    ])
    approvalTemplateOptions.value = normalizeTemplateTypeOptions(templateRes)
    const approvalRows = normalizeApiList(approvalRes).map(normalizeApprovalAppealRow)
    const attendanceRows = normalizeApiList(attendanceRes)
      .filter((r: any) => isExceptionStatus(normalizeStatus(r)))
      .map(normalizeAttendanceAnomalyRow)
    anomalyList.value = [...approvalRows, ...attendanceRows]
      .sort((a: any, b: any) => String(b.sort_time || '').localeCompare(String(a.sort_time || '')))
  } catch {
    ElMessage.error('考勤审批加载失败')
  } finally {
    anomalyLoading.value = false
  }
}

function normalizeTemplateTypeOptions(payload: any) {
  const groups = Array.isArray(payload) ? payload : []
  const options: Array<{ value: string; label: string }> = []
  groups.forEach((group: any) => {
    const templates = Array.isArray(group?.templates) ? group.templates : []
    templates.forEach((template: any) => {
      const value = String(template.business_code || template.code || template.id || '').trim()
      const label = String(template.name || template.label || value).trim()
      if (value && label) options.push({ value, label })
    })
  })
  return options
}

function normalizeApprovalAppealRow(row: any) {
  const formData = row.form_data && typeof row.form_data === 'object' ? row.form_data : {}
  const typeValue = String(row.business_type || row.module || '').trim()
  const typeLabel = row.approval_type_name || templateLabel(typeValue) || row.summary || typeValue || '审批申请'
  return {
    ...row,
    row_key: `approval-${row.id}`,
    source: 'approval',
    approval_id: row.id,
    employee_name: row.applicant_name || employeeMap.value[row.applicant_id]?.name || `员工#${row.applicant_id || '-'}`,
    type_value: typeValue,
    type_label: typeLabel,
    created_at: formatDateTime(row.created_at),
    sort_time: row.created_at,
    related_date: inferRelatedDate(formData),
    punch_time: inferBusinessTime(formData),
    summary: row.summary || inferSummary(formData) || typeLabel,
    current_handler: row.status === 'pending' ? `第 ${row.current_node_order || 1} 节点待处理` : '-',
  }
}

function normalizeAttendanceAnomalyRow(row: any) {
  const employee = employeeMap.value[row.employee_id] || {}
  const statusText = normalizeStatus(row)
  const punchTime = row.clock_in_time || row.clock_out_time
  return {
    ...row,
    row_key: `attendance-anomaly-${row.id}`,
    source: 'attendance_anomaly',
    employee_name: row.employee_name || employee.name || `员工#${row.employee_id || '-'}`,
    type_value: 'attendance_anomaly',
    type_label: statusText || '打卡异常',
    status: 'attendance_anomaly',
    created_at: row.date || '',
    sort_time: row.date || '',
    related_date: row.date || '',
    punch_time: formatDateTime(punchTime) || formatApiClockTime(punchTime) || '-',
    summary: row.note || row.anomaly_type || statusText || '待员工提交审批申请',
    current_handler: '未关联审批',
    form_data: {
      date: row.date,
      status: statusText,
      clock_in_time: row.clock_in_time,
      clock_out_time: row.clock_out_time,
      note: row.note,
    },
  }
}

function templateLabel(value: string) {
  return approvalTemplateOptions.value.find((item) => item.value === value)?.label || ''
}

function inferRelatedDate(formData: Record<string, any>) {
  const keys = ['date', 'apply_date', 'attendance_date', 'correction_date', 'start_date', 'start_time', 'begin_date', 'begin_time', 'out_date', 'trip_start_time']
  for (const key of keys) {
    const text = datePart(formData[key])
    if (text) return text
  }
  return ''
}

function inferBusinessTime(formData: Record<string, any>) {
  const keys = ['punch_time', 'clock_time', 'start_time', 'end_time', 'begin_time', 'finish_time', 'trip_time', 'time_range']
  const values = keys.map((key) => fieldDisplayText(formData[key])).filter(Boolean)
  return values.slice(0, 2).join(' 至 ')
}

function inferSummary(formData: Record<string, any>) {
  const keys = ['reason', 'remark', 'description', 'note', 'cause', 'leave_reason', 'out_reason', 'trip_reason']
  for (const key of keys) {
    const text = fieldDisplayText(formData[key])
    if (text) return text
  }
  return ''
}

function datePart(value: any) {
  if (!value) return ''
  const text = String(value)
  const match = text.match(/\d{4}[-/]\d{1,2}[-/]\d{1,2}/)
  return match ? match[0].replace(/\//g, '-') : ''
}

function fieldLabelText(key: string) {
  const known: Record<string, string> = {
    date: '日期',
    status: '状态',
    reason: '原因',
    remark: '备注',
    note: '说明',
    start_time: '开始时间',
    end_time: '结束时间',
    punch_time: '打卡时间',
    punch_type: '打卡类型',
    clock_in_time: '上班打卡',
    clock_out_time: '下班打卡',
    review_comment: '审批意见',
  }
  return known[key] || key.replace(/_/g, ' ')
}

function fieldDisplayText(value: any): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map(fieldDisplayText).filter(Boolean).join('、')
  if (typeof value === 'object') {
    return String(value.label || value.name || value.title || value.value || JSON.stringify(value))
  }
  return String(value)
}

function approvalStatusText(status: string) {
  const map: Record<string, string> = {
    pending: '审批中',
    approved: '已通过',
    rejected: '已驳回',
    withdrawn: '已撤回',
    cancelled: '已撤回',
    attendance_anomaly: '待申诉',
  }
  return map[String(status || '')] || status || '-'
}

function approvalStatusTagType(status: string) {
  const map: Record<string, string> = {
    pending: 'warning',
    approved: 'success',
    rejected: 'danger',
    withdrawn: 'info',
    cancelled: 'info',
    attendance_anomaly: 'info',
  }
  return (map[String(status || '')] || 'info') as any
}

function approvalActionText(action: string) {
  const map: Record<string, string> = {
    approve: '通过',
    reject: '拒绝',
    transfer: '转审',
    withdraw: '撤回',
  }
  return map[String(action || '')] || action || '处理'
}

function appealSourceText(source: string) {
  const map: Record<string, string> = {
    approval: '审批申请',
    attendance_anomaly: '打卡异常',
  }
  return map[String(source || '')] || source || '-'
}

async function openAppealDetail(row: any) {
  appealDetailVisible.value = true
  appealDetailLoading.value = true
  appealDetail.value = { ...row }
  try {
    if (row.source === 'approval' && row.approval_id) {
      const detail: any = await get(`/approval/instances/${row.approval_id}`)
      const pendingNode = Array.isArray(detail.progress_nodes)
        ? detail.progress_nodes.find((node: any) => node.status === 'current' || node.status === 'pending')
        : null
      appealDetail.value = {
        ...normalizeApprovalAppealRow(detail),
        ...detail,
        source: 'approval',
        type_label: detail.approval_type_name || row.type_label,
        current_handler: pendingNode ? `${pendingNode.node_name || '当前节点'} · ${pendingNode.approver_display || pendingNode.resolve_message || '-'}` : row.current_handler,
        created_at: formatDateTime(detail.created_at),
      }
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '详情加载失败')
  } finally {
    appealDetailLoading.value = false
  }
}

// ============ 导出月报 ============
function exportMonthlyReport() {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1
  const url = `/api/v1/attendance/export-monthly?year=${year}&month=${month}`
  const a = document.createElement('a')
  a.href = url
  a.download = `attendance_${year}${String(month).padStart(2, '0')}.xlsx`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// ============ Tab change & init ============
function fetchActiveReport() {
  if (reportTab.value === 'summary') {
    if (summaryTaskId.value && summaryTaskStatus.value === 'succeeded') {
      loadSummaryTaskResult()
      return
    }
    fetchSummary()
    return
  }
  fetchDailyReport()
}

function handleReportTabChange(tab: string) {
  reportTab.value = tab === 'summary' ? 'summary' : 'daily-report'
  fetchActiveReport()
}

function handleTabChange(tab: string) {
  const m: Record<string, () => void> = {
    records: fetchRecords,
    'punch-time-records': fetchPunchTimeRecords,
    rules: fetchRules,
    reports: fetchActiveReport,
    anomalies: fetchAnomalies,
  }
  m[tab]?.()
}

function normalizeApiList(payload: any): any[] {
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.items)) return payload.items
  if (Array.isArray(payload?.data)) return payload.data
  if (Array.isArray(payload?.data?.items)) return payload.data.items
  return []
}

const EMPLOYEE_OPTION_PAGE_SIZE = 1000
const ATTENDANCE_PICKER_EMPLOYEE_STATUSES = ['在职', '试用期', '待入职']

function normalizeApiTotal(payload: any): number | null {
  const total = Number(payload?.total ?? payload?.data?.total)
  return Number.isFinite(total) ? total : null
}

async function fetchEmployeeOptionsByStatus(status: string) {
  const rows: any[] = []
  let page = 1
  let total: number | null = null

  while (true) {
    const res = await get('/employees', { status, page, page_size: EMPLOYEE_OPTION_PAGE_SIZE })
    const items = normalizeApiList(res)
    total = normalizeApiTotal(res)
    rows.push(...items)
    if (!items.length) break
    if (total !== null && rows.length >= total) break
    if (items.length < EMPLOYEE_OPTION_PAGE_SIZE) break
    page += 1
  }

  return rows
}

async function fetchAllEmployeesForAttendancePicker() {
  const rows: any[] = []
  const seenIds = new Set<number>()
  for (const status of ATTENDANCE_PICKER_EMPLOYEE_STATUSES) {
    const items = await fetchEmployeeOptionsByStatus(status)
    for (const item of items) {
      const id = Number(item?.id)
      if (!Number.isFinite(id) || seenIds.has(id)) continue
      seenIds.add(id)
      rows.push(item)
    }
  }
  return rows
}

async function fetchOptions() {
  try {
    const [, employees, companyRes, companyLayoutRes] = await Promise.all([
      ensureDepartmentsLoaded(),
      fetchAllEmployeesForAttendancePicker(),
      get('/payroll/companies', { active_only: false }).catch(() => []),
      get('/organizations/company-layouts').catch(() => []),
    ])
    employeeOptions.value = employees
    employeeMap.value = Object.fromEntries(employeeOptions.value.map((e: any) => [e.id, e]))
    assigneeCompanies.value = normalizeApiList(companyRes)
    assigneeCompanyLayouts.value = normalizeApiList(companyLayoutRes)
  } catch {}
}

async function resumeSummaryTaskFromStorage() {
  const raw = localStorage.getItem(SUMMARY_TASK_STORAGE_KEY)
  if (!raw) return
  try {
    const parsed = JSON.parse(raw)
    const taskId = parsed?.task_id
    if (!taskId) return
    const requestId = ++summaryFetchSeq.value
    const task = await get(`/attendance/monthly-report/tasks/${taskId}`, undefined, { skipDefaultErrorHandler: true })
    if (requestId !== summaryFetchSeq.value) return
    applySummaryTask(task, { restoreQuery: true })
    if (task.status === 'succeeded') {
      await loadSummaryTaskResult(taskId)
    } else if (task.status === 'pending' || task.status === 'running') {
      startSummaryTaskPolling(taskId, requestId)
    }
  } catch {
    localStorage.removeItem(SUMMARY_TASK_STORAGE_KEY)
  }
}

onMounted(async () => {
  await fetchOptions()
  loadDailyReportSettings()
  loadSummaryReportSettings()
  loadStatusOverrides()
  await resumeSummaryTaskFromStorage()
  handleTabChange(activeTab.value)
  fetchTodayStats()
  autoRefreshTimer.value = window.setInterval(() => {
    if (activeTab.value === 'records') fetchRecords()
    if (activeTab.value === 'anomalies') fetchAnomalies()
    if (activeTab.value === 'reports') fetchActiveReport()
    fetchTodayStats()
  }, 60 * 1000)
})

onBeforeUnmount(() => {
  if (autoRefreshTimer.value) window.clearInterval(autoRefreshTimer.value)
  stopSummaryTaskPolling()
})
</script>

<style scoped>
.attendance-page {
  padding: 24px;
  background: #F1F5F9;
  min-height: 100vh;
}

/* Page Header */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}
.page-header__left {
  display: flex;
  flex-direction: column;
}
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: #1E293B;
  margin: 0 0 4px 0;
}
.page-desc {
  font-size: 14px;
  color: #64748B;
  margin: 0;
}

/* Stat Cards */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.stat-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  transition: box-shadow 0.2s;
}
.stat-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.stat-card__icon {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-card__info {
  display: flex;
  flex-direction: column;
}
.stat-card__value {
  display: flex;
  align-items: baseline;
}
.stat-card__num {
  font-size: 28px;
  font-weight: 700;
  color: #1E293B;
  line-height: 1.2;
}
.stat-card__num--warning { color: #F59E0B; }
.stat-card__num--success { color: #10B981; }
.stat-card__num--indigo { color: #6366F1; }
.stat-card__total {
  font-size: 16px;
  font-weight: 500;
  color: #94A3B8;
  margin-left: 2px;
}
.stat-card__label {
  font-size: 13px;
  color: #94A3B8;
  margin-top: 4px;
}

.trend-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}

.trend-card {
  border-radius: 12px;
  border: 1px solid #E2E8F0;
}

.quick-filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.quick-filter-row .el-tag {
  cursor: pointer;
}

.batch-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #F8FAFC;
  border: 1px solid #E2E8F0;
  border-radius: 10px;
  padding: 8px 12px;
  margin-bottom: 10px;
}

/* Content Card */
.content-card {
  background: #fff;
  border-radius: 12px;
  padding: 4px 24px 24px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

/* Custom Tabs */
.custom-tabs :deep(.el-tabs__header) {
  margin-bottom: 20px;
}
.custom-tabs :deep(.el-tabs__item) {
  font-size: 14px;
  font-weight: 500;
  height: 48px;
  line-height: 48px;
  color: #64748B;
}
.custom-tabs :deep(.el-tabs__item.is-active) {
  color: #4F46E5;
}
.custom-tabs :deep(.el-tabs__active-bar) {
  background-color: #4F46E5;
}

/* Filter Row */
.filter-row {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.rule-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.rule-form-stack {
  --rule-form-label-width: 132px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.rule-form-stack :deep(.el-form-item__label) {
  color: #1F2937;
  font-size: 15px;
  font-weight: 600;
  line-height: 34px;
  padding-right: 16px;
  white-space: nowrap;
}
.rule-form-stack :deep(.el-form-item__content) {
  min-width: 0;
}
.rule-module {
  border: 1px solid #E5E7EB;
  border-radius: 10px;
  background: #fff;
  padding: 18px 18px 16px;
}
.rule-module :deep(.el-form-item) {
  margin-bottom: 20px;
}
.rule-module :deep(.el-form-item:last-of-type) {
  margin-bottom: 0;
}
.rule-module__title {
  font-size: 18px;
  line-height: 26px;
  margin-bottom: 18px;
  color: #111827;
  font-weight: 700;
}
.required-star {
  color: #EF4444;
  margin-right: 2px;
}
.rule-summary-card {
  background: #F8FAFC;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  color: #334155;
  line-height: 1.7;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.attendance-setting-tip {
  margin-bottom: 20px;
  color: #6B7280;
  font-size: 14px;
  line-height: 22px;
}
.attendance-setting-tip__link {
  margin-left: 2px;
  padding: 0;
  height: auto;
}
.attendance-setting-row {
  display: grid;
  grid-template-columns: var(--rule-form-label-width) minmax(0, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}
.attendance-setting-row:last-child {
  margin-bottom: 0;
}
.attendance-setting-row__label {
  color: #1F2937;
  font-size: 15px;
  font-weight: 600;
  line-height: 34px;
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}
.attendance-setting-row__info {
  margin-left: 4px;
  color: #9CA3AF;
  font-size: 15px;
}
.attendance-setting-row__content {
  min-width: 0;
}
.attendance-setting-row__main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 34px;
}
.attendance-setting-row__main--wrap {
  flex-wrap: wrap;
}
.attendance-setting-row__desc {
  color: #6B7280;
  font-size: 14px;
  line-height: 22px;
}
.retroactive-setting-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(2, minmax(260px, 1fr));
  gap: 12px 18px;
}
.retroactive-setting-grid label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #374151;
  font-size: 14px;
  white-space: nowrap;
}
.retroactive-setting-grid :deep(.el-input-number) {
  width: 120px;
}
.retroactive-setting-grid :deep(.el-checkbox) {
  grid-column: 1 / -1;
  margin-right: 0;
}
.attendance-patch-card {
  margin-top: 12px;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  background: #F8FAFC;
  padding: 16px 16px 12px;
}
.attendance-patch-card__line {
  display: grid;
  grid-template-columns: 160px minmax(0, 260px) minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.attendance-patch-card__line--types {
  grid-template-columns: 160px minmax(0, 1fr);
}
.attendance-patch-card__line-label {
  color: #1F2937;
  font-size: 14px;
  line-height: 22px;
}
.attendance-patch-card__types {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
}
.attendance-patch-card__types :deep(.el-checkbox) {
  margin-right: 0;
}
.attendance-setting-select {
  width: 180px;
}
.attendance-patch-card__line-tip {
  color: #6B7280;
  font-size: 14px;
  line-height: 22px;
}
.attendance-setting-summary {
  margin-top: 12px;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  background: #F8FAFC;
  min-height: 56px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 14px;
}
.attendance-setting-summary__title {
  color: #1F2937;
  font-size: 14px;
}
.attendance-setting-summary__value {
  color: #6B7280;
  font-size: 14px;
}
.leave-punch-setting-dialog :deep(.el-dialog__body) {
  padding-top: 10px;
}
.leave-punch-setting-subtitle {
  color: #6B7280;
  font-size: 14px;
  line-height: 22px;
}
.leave-punch-setting-link {
  margin-left: 4px;
  padding: 0;
  height: auto;
}
.leave-punch-setting-check {
  margin-top: 16px;
}
.leave-punch-setting-hint {
  margin: 4px 0 0 32px;
  color: #6B7280;
  font-size: 14px;
  line-height: 22px;
}
.leave-punch-setting-panel {
  margin-top: 14px;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  background: #F3F4F6;
  min-height: 66px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: #374151;
  font-size: 14px;
}
.leave-punch-setting-panel.is-disabled {
  opacity: .7;
}
.leave-punch-setting-select {
  width: 190px;
}
.overtime-setting-block {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.overtime-setting-row {
  display: grid;
  grid-template-columns: var(--rule-form-label-width) 1fr;
  gap: 12px;
  align-items: start;
}
.overtime-setting-row__label {
  color: #334155;
  font-size: 15px;
  font-weight: 600;
  line-height: 34px;
  white-space: nowrap;
}
.overtime-setting-row__value {
  display: flex;
  flex-direction: column;
}
.overtime-setting-row__action {
  margin-bottom: 12px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.overtime-rule-tabs {
  display: inline-flex;
  gap: 30px;
  border-bottom: 1px solid #E5E7EB;
  padding-bottom: 8px;
  margin-bottom: 18px;
}
.overtime-rule-tab {
  border: none;
  padding: 0;
  background: transparent;
  color: #6B7280;
  font-size: 30px;
  line-height: 1;
  cursor: pointer;
  font-weight: 500;
}
.overtime-rule-tab.is-active {
  color: #2F80ED;
  font-weight: 600;
}
.overtime-rule-body {
  max-height: 65vh;
  overflow: auto;
  padding-right: 6px;
}
.overtime-rule-line {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px;
  margin-bottom: 12px;
}
.overtime-rule-line--allow {
  grid-template-columns: 1fr;
}
.overtime-rule-line__label {
  color: #334155;
  font-size: 16px;
  line-height: 34px;
}
.overtime-rule-holiday-tip {
  margin-left: 12px;
  color: #64748B;
  font-size: 14px;
}
.overtime-method-cards {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.overtime-method-card {
  width: 240px;
  min-height: 100px;
  border: 1px solid #D1D5DB;
  border-radius: 6px;
  padding: 12px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.overtime-method-card strong {
  color: #1F2937;
  font-size: 16px;
  font-weight: 600;
}
.overtime-method-card span {
  color: #6B7280;
  font-size: 14px;
  line-height: 1.5;
}
.overtime-method-card.is-active {
  border-color: #3B82F6;
  background: #EFF6FF;
}
.overtime-rule-panel {
  background: #F3F4F6;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.overtime-inline-row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #334155;
  font-size: 16px;
  flex-wrap: wrap;
}
.overtime-rest-period-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.overtime-divider {
  border-top: 1px solid #E5E7EB;
}
.rule-form-block {
  border: 1px solid #E2E8F0;
  border-radius: 10px;
  padding: 14px 14px 4px;
}
.rule-form-block--full {
  grid-column: 1 / -1;
}
.rule-form-title {
  font-weight: 600;
  color: #334155;
  margin-bottom: 10px;
}
.rule-type-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.assignee-picker {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.assignee-add-btn {
  width: 74px;
  min-width: 74px;
  align-self: flex-start;
  padding: 8px 18px;
}
.assignee-picker__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 30px;
  align-items: center;
}
.assignee-picker__summary {
  width: 100%;
  min-height: 42px;
  border: 1px solid #DCE3EF;
  border-radius: 8px;
  padding: 10px 12px;
  color: #475569;
  background: #fff;
}
.assignee-tree-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.assignee-tree-panel__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}
.assignee-tree-panel__search {
  flex: 1;
}
.assignee-tree {
  max-height: 320px;
  overflow: auto;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  padding: 8px 10px;
  background:
    linear-gradient(90deg, rgba(79, 70, 229, 0.05) 0 1px, transparent 1px) 22px 0 / 24px 100% no-repeat,
    #fff;
}
.assignee-tree :deep(.el-tree-node__content) {
  min-height: 38px;
  border-radius: 8px;
  transition: background-color 0.18s ease;
}
.assignee-tree :deep(.el-tree-node__content:hover) {
  background: #F8FAFC;
}
.assignee-tree-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: #334155;
  font-size: 14px;
}
.assignee-tree-node__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  background: #EEF2FF;
  color: #4F46E5;
  font-size: 12px;
  font-weight: 800;
}
.assignee-tree-node--company .assignee-tree-node__badge {
  background: #E0F2FE;
  color: #0369A1;
}
.assignee-tree-node--department .assignee-tree-node__badge {
  background: #ECFDF5;
  color: #047857;
}
.assignee-tree-node--employee .assignee-tree-node__badge {
  background: #FFF7ED;
  color: #C2410C;
}
.assignee-tree-node__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.assignee-tree-node__meta {
  color: #64748B;
  font-size: 12px;
}
.assignee-tree-node__count {
  margin-left: 4px;
  border-radius: 999px;
  background: #F1F5F9;
  color: #64748B;
  font-size: 12px;
  font-weight: 700;
  padding: 2px 7px;
}
.rule-type-card {
  border: 1px solid #DCE3EF;
  border-radius: 10px;
  padding: 12px;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
}
.rule-type-card.is-active {
  border-color: #3B82F6;
  background: #EFF6FF;
  box-shadow: inset 0 0 0 1px rgba(59, 130, 246, 0.25);
}
.rule-type-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #0F172A;
}
.rule-type-card__desc {
  margin-top: 8px;
  font-size: 12px;
  color: #64748B;
  line-height: 1.5;
}
.free-rule-tip {
  padding: 10px 12px;
  border-radius: 8px;
  background: #F8FAFC;
  color: #64748B;
  margin-bottom: 12px;
  font-size: 13px;
}
.free-time-panel {
  border: 1px solid #E5E7EB;
  background: #F5F6F7;
  border-radius: 6px;
  width: 100%;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.free-time-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  color: #374151;
}
.free-time-label {
  width: 96px;
  color: #111827;
  font-weight: 500;
  flex: 0 0 96px;
}
.free-time-tip {
  color: #6B7280;
  font-size: 14px;
}
.segment-summary-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 6px;
}
.time-table-wrap {
  margin: -2px 0 12px var(--rule-form-label-width);
  width: calc(100% - var(--rule-form-label-width));
}
.time-table {
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  overflow: hidden;
  margin: 0;
}
.time-table__head {
  display: grid;
  grid-template-columns: 260px 1fr;
  background: #F3F4F6;
  color: #6B7280;
  font-weight: 600;
}
.time-table__head > div {
  padding: 10px 16px;
}
.time-table__row {
  display: grid;
  grid-template-columns: 260px 1fr 110px;
  border-top: 1px solid #E5E7EB;
  background: #fff;
}
.time-table__weekday {
  padding: 16px;
  color: #334155;
  display: flex;
  align-items: center;
}
.time-table__detail {
  padding: 12px 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #334155;
  min-width: 0;
}
.time-table__line {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.time-table__label {
  width: 90px;
  color: #6B7280;
  flex-shrink: 0;
}
.time-table__line > span:last-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time-table__actions {
  padding: 14px 12px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.time-table__foot {
  border-top: 1px solid #E5E7EB;
  background: #fff;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #6B7280;
}
.segment-summary-row {
  border: 1px solid #E2E8F0;
  border-radius: 10px;
  padding: 12px;
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.segment-summary-row__left {
  flex: 1;
}
.segment-summary-row__title {
  color: #0F172A;
  font-weight: 600;
  margin-bottom: 6px;
}
.segment-summary-row__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: #475569;
  font-size: 12px;
  margin-bottom: 4px;
}
.segment-summary-row__actions {
  min-width: 82px;
  text-align: right;
}
.segment-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.segment-row {
  display: grid;
  grid-template-columns: 112px 1fr;
  gap: 10px;
  align-items: start;
}
.segment-label {
  color: #334155;
  font-size: 15px;
  line-height: 34px;
}
.segment-value {
  min-height: 34px;
  color: #1E293B;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.segment-panel {
  background: #F3F4F6;
  border-radius: 6px;
  border: 1px solid #EBEEF5;
  padding: 12px 14px;
}
.segment-divider {
  border-top: 1px solid #E5E7EB;
  margin: 10px 0;
}
.segment-tip {
  color: #6B7280;
  font-size: 13px;
}
.segment-tip--between {
  display: flex;
  justify-content: space-between;
}
.segment-tip-muted {
  color: #9CA3AF;
  font-size: 13px;
}
.segment-time-line {
  display: grid;
  grid-template-columns: 84px 290px 140px;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.segment-time-line:last-child {
  margin-bottom: 0;
}
.segment-extra-period {
  border-top: 1px dashed #D1D5DB;
  margin-top: 10px;
  padding-top: 10px;
}
.segment-extra-period__action {
  text-align: right;
  margin-top: -4px;
}
.segment-time-title {
  color: #334155;
}
.segment-time-line--rest {
  grid-template-columns: 84px auto auto 1fr;
}
.segment-footer {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.rest-time-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rest-time-row {
  display: grid;
  grid-template-columns: 96px 1fr 24px 1fr 88px;
  align-items: center;
  gap: 8px;
}
.rest-time-row__label {
  color: #334155;
}
.rest-time-row__ops {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}
.punch-window-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.punch-window-section {
  border-bottom: 1px solid #F1F5F9;
  padding-bottom: 10px;
  position: relative;
}
.punch-window-section:last-child {
  border-bottom: none;
}
.punch-window-section__title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 8px;
}
.punch-window-row {
  display: grid;
  grid-template-columns: 180px 1fr 24px 1fr;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.punch-window-row__hint {
  margin: -2px 0 12px 188px;
  color: #9CA3AF;
  font-size: 13px;
}
.punch-window-row__label {
  color: #334155;
}
.punch-window-row__remove {
  position: absolute;
  right: 0;
  top: 26px;
}
.copy-segment-list {
  max-height: 480px;
  overflow: auto;
}
.copy-segment-item {
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.copy-segment-item__rule {
  font-size: 15px;
  font-weight: 600;
  color: #334155;
}
.copy-segment-item__detail {
  color: #475569;
  margin-top: 3px;
}
.copy-segment-item :deep(.el-checkbox__label) {
  width: 100%;
}
.biweekly-panel {
  width: 100%;
}
.biweekly-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.biweekly-row__label {
  width: 42px;
  color: #64748B;
  font-size: 13px;
}
.segment-editor-dialog :deep(.el-dialog__body) {
  padding-top: 12px;
}
.work-segment__header,
.special-date-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.special-date-list {
  margin-top: 10px;
  border: 1px dashed #D1D5DB;
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.special-date-item {
  color: #334155;
  font-size: 13px;
}
.special-date-inline {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: -4px;
  margin-bottom: 8px;
}
.special-date-inline__block {
  background: #F8FAFC;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  padding: 10px;
}
.special-date-inline__title {
  font-size: 12px;
  color: #475569;
  font-weight: 600;
}
.date-action-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.tag-wrap {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.popup-manage {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.popup-manage__items {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.import-entry-row {
  margin-bottom: 10px;
  color: #94A3B8;
  display: flex;
  align-items: center;
  gap: 8px;
}
.popup-form-row {
  display: grid;
  gap: 8px;
  margin-bottom: 12px;
}
.popup-form-row--3 {
  grid-template-columns: 150px minmax(180px, 1fr) 130px;
}
.popup-form-row--2 {
  grid-template-columns: 1fr 1fr;
}
.location-geo-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.location-provider-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.location-provider-row__label {
  color: #64748B;
  font-size: 13px;
  flex-shrink: 0;
}
.location-suggest-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.3;
  padding: 2px 0;
}
.location-suggest-item__title {
  color: #111827;
  font-size: 13px;
  font-weight: 500;
}
.location-suggest-item__address {
  color: #6B7280;
  font-size: 12px;
}
.wework-location-dialog :deep(.el-dialog__body) {
  padding-top: 18px;
  padding-bottom: 14px;
}
.wework-location-body {
  min-height: 172px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.wework-location-row {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 10px;
  align-items: center;
}
.wework-location-country {
  width: 140px;
}
.wework-location-search {
  width: 100%;
}
.wework-location-links {
  margin-top: 22px;
  justify-content: center;
  color: #94A3B8;
}
.wework-location-build-tag {
  margin-top: 4px;
  text-align: center;
  font-size: 11px;
  color: #D1D5DB;
}
.wework-location-suggest-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  line-height: 30px;
  min-height: 30px;
}
.wework-location-suggest-row__name {
  color: #111827;
  flex: 0 0 auto;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wework-location-suggest-row__addr {
  color: #6B7280;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
:global(.wework-location-suggest-popper .el-autocomplete-suggestion__list li) {
  padding: 0 12px;
}
:global(.wework-location-suggest-popper .el-autocomplete-suggestion__wrap) {
  max-height: 220px;
}
.location-geo-row__text {
  color: #64748B;
  font-size: 13px;
}
.location-geo-row__input {
  width: 140px;
}
.location-geo-row__text strong {
  color: #111827;
  font-weight: 600;
}
.calendar-preview-header {
  display: flex;
  justify-content: center;
  margin-bottom: 10px;
}
.calendar-preview-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  overflow: hidden;
}
.calendar-head {
  background: #F8FAFC;
  color: #64748B;
  text-align: center;
  font-size: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #E2E8F0;
}
.calendar-cell {
  text-align: center;
  padding: 9px 0;
  border-bottom: 1px solid #F1F5F9;
  border-right: 1px solid #F1F5F9;
  color: #334155;
}
.calendar-cell:nth-child(7n) {
  border-right: none;
}
.calendar-cell.is-muted {
  color: #CBD5E1;
}
.calendar-cell.is-workday {
  background: #EAF2FF;
  color: #2563EB;
  font-weight: 600;
}
.calendar-preview-legend {
  margin-top: 10px;
  display: flex;
  gap: 18px;
  justify-content: center;
  color: #64748B;
  font-size: 12px;
}
.dot {
  width: 8px;
  height: 8px;
  display: inline-block;
  border-radius: 50%;
  margin-right: 6px;
}
.dot-work {
  background: #2563EB;
}
.dot-rest {
  background: #CBD5E1;
}
.special-time-lines {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 6px;
}
.special-time-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.shift-template-builder {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) 140px 140px 70px;
  gap: 8px;
  margin-bottom: 12px;
}
.field-unit {
  margin-left: 8px;
  color: #64748B;
  font-size: 12px;
}
.hint-text {
  margin-top: 6px;
  color: #64748B;
  font-size: 12px;
}
.punch-method-tip {
  margin: 6px 0 16px;
}
.field-label-with-tip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 15px;
  font-weight: 600;
  line-height: 34px;
  white-space: nowrap;
}
.field-info-icon {
  color: #9CA3AF;
  font-size: 15px;
}
.punch-choice-group {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
}
.punch-choice-item {
  width: 100%;
  display: grid;
  grid-template-columns: 170px 1fr;
  column-gap: 8px;
  align-items: center;
}
.punch-choice-item :deep(.el-radio) {
  margin-right: 0;
}
.punch-choice-item__desc {
  color: #6B7280;
  font-size: 14px;
  line-height: 22px;
}
.more-setting-line {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.more-setting-remind {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.outside-punch-mode-group {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: flex-start;
}
.outside-punch-mode-group :deep(.el-radio) {
  margin-right: 0;
  white-space: normal;
  line-height: 24px;
}
.more-setting-picker {
  width: 100%;
  display: flex;
  flex-direction: row;
  gap: 10px;
  align-items: flex-start;
}
.more-setting-picker__selected {
  flex: 1;
  min-height: 42px;
  border: 1px solid #DCE3EF;
  border-radius: 8px;
  padding: 7px 10px;
  background: #fff;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.more-setting-picker__placeholder {
  color: #94A3B8;
  font-size: 14px;
}
.hint-text--danger {
  color: #EF4444;
}
.hint-link {
  color: #2563EB;
  margin-left: 8px;
  text-decoration: none;
}
.hint-link:hover {
  text-decoration: underline;
}
.holiday-desc,
.restday-desc {
  margin-left: 10px;
  color: #6B7280;
  font-size: 14px;
}
.restday-setting-dialog :deep(.el-dialog__body) {
  padding-top: 12px;
}
.restday-setting-subtitle {
  margin-bottom: 22px;
  color: #6B7280;
  font-size: 14px;
}
.restday-setting-form :deep(.el-form-item) {
  margin-bottom: 20px;
}
.restday-shift-mode {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.restday-shift-mode__row {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  column-gap: 24px;
  align-items: start;
  min-height: 32px;
}
.restday-shift-mode__option {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  column-gap: 10px;
  align-items: start;
  min-width: 0;
  cursor: pointer;
}
.restday-shift-mode__option :deep(.el-radio) {
  margin: 0;
  width: 20px;
  min-width: 20px;
  display: inline-flex;
  align-items: flex-start;
}
.restday-shift-mode__option :deep(.el-radio__label) {
  display: none;
}
.restday-shift-mode__option :deep(.el-radio__input) {
  margin-top: 2px;
}
.restday-shift-mode__choice {
  color: #334155;
  font-size: 14px;
  line-height: 22px;
  white-space: nowrap;
}
.restday-shift-mode__option.is-active .restday-shift-mode__choice {
  color: #4F46E5;
  font-weight: 600;
}
.restday-shift-mode__desc {
  color: #6B7280;
  font-size: 14px;
  line-height: 22px;
  padding-top: 0;
  flex: 1 1 auto;
  min-width: 0;
}
.restday-interval {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #334155;
  font-size: 14px;
}
.time-table__more {
  font-size: 18px;
  line-height: 1;
  color: #64748B;
}
.shift-setting-summary {
  margin: 0 0 8px var(--rule-form-label-width);
  border: 1px solid #E5E7EB;
  background: #F8FAFC;
  border-radius: 6px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.shift-setting-summary div {
  display: flex;
  gap: 16px;
  color: #475569;
}
.shift-setting-summary span {
  width: 64px;
  color: #334155;
}
.shift-setting-summary strong {
  color: #475569;
  font-weight: 500;
}
.shift-arrange-dialog :deep(.el-dialog__body) {
  padding-top: 12px;
}
.shift-arrange-panel {
  background: #fff;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  padding: 14px 18px 10px;
  margin-bottom: 16px;
}
.shift-arrange-row {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 10px;
  align-items: start;
  margin-bottom: 14px;
}
.shift-arrange-label {
  color: #334155;
  font-weight: 600;
  line-height: 34px;
}
.shift-arrange-mode-cards {
  display: flex;
  gap: 10px;
}
.shift-arrange-mode-card {
  min-width: 260px;
  border: 1px solid #D1D5DB;
  border-radius: 6px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}
.shift-arrange-mode-card.is-active {
  border-color: #3B82F6;
  background: #EFF6FF;
}
.shift-arrange-mode-card span {
  color: #64748B;
}
.shift-class-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.shift-arrange-more {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.shift-match-window {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #334155;
}
.shift-roster-table {
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  background: #fff;
  padding: 12px;
}
.shift-roster-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.shift-roster-table table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.shift-roster-table th,
.shift-roster-table td {
  border: 1px solid #EEF2FF;
  text-align: center;
  padding: 6px 4px;
  font-size: 12px;
  color: #334155;
}
.shift-roster-table th:first-child,
.shift-roster-table td.name-col {
  width: 110px;
  background: #F8FAFC;
}
.shift-roster-table .empty-cell {
  color: #94A3B8;
  padding: 32px 0;
}
.shift-class-drawer :deep(.el-drawer__body) {
  display: flex;
  flex-direction: column;
}
.shift-class-drawer__toolbar {
  margin-bottom: 12px;
}
.shift-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
}
.holiday-calendar-dialog :deep(.el-dialog__body) {
  padding-top: 16px;
}
.holiday-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.holiday-source {
  color: #6B7280;
  font-size: 13px;
}
.holiday-legend {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 18px;
  color: #374151;
}
.legend-dot {
  width: 14px;
  height: 14px;
  border-radius: 4px;
  display: inline-block;
  margin-right: 6px;
  border: 1px solid #E5E7EB;
}
.legend-work {
  background: #EAF2FF;
  border-color: #EAF2FF;
}
.legend-rest {
  background: #fff;
}
.holiday-month-switch {
  height: 58px;
  border: 1px solid #E5E7EB;
  border-bottom: none;
  border-radius: 10px 10px 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}
.holiday-month-switch strong {
  font-size: 34px;
  font-weight: 700;
  color: #111827;
}
.holiday-grid {
  border: 1px solid #E5E7EB;
  border-radius: 0 0 10px 10px;
  overflow: hidden;
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
}
.holiday-grid__head {
  height: 52px;
  background: #fff;
  border-bottom: 1px solid #E5E7EB;
  color: #6B7280;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.holiday-grid__cell {
  min-height: 112px;
  padding: 16px 14px;
  border-right: 1px solid #E5E7EB;
  border-bottom: 1px solid #E5E7EB;
  background: #fff;
}
.holiday-grid__cell.is-last-col {
  border-right: none;
}
.holiday-grid__cell.is-workday {
  background: #EAF2FF;
}
.holiday-grid__cell.is-restday {
  background: #fff;
}
.holiday-grid__cell.is-out {
  opacity: 0.45;
}
.holiday-grid__cell.is-selected {
  box-shadow: inset 0 0 0 2px #3B82F6;
}
.holiday-grid__day {
  font-size: 16px;
  font-weight: 600;
  color: #111827;
}
.holiday-grid__tag {
  margin-top: 6px;
  color: #16A34A;
  font-size: 13px;
  font-weight: 600;
}

/* Modern Table */
.modern-table {
  --el-table-border-color: #E2E8F0;
  --el-table-header-bg-color: #F8FAFC;
  --el-table-row-hover-bg-color: #F8FAFC;
  border: 1px solid #E2E8F0;
  border-radius: 10px;
  overflow: hidden;
}
.modern-table :deep(.el-table__header th) {
  background-color: #F1F5F9 !important;
  color: #64748B;
  font-weight: 600;
  font-size: 13px;
  border-bottom: 1px solid #E2E8F0;
  padding: 14px 0;
}
.modern-table :deep(.el-table__body td) {
  border-bottom: 1px solid #F1F5F9;
  padding: 12px 0;
}
.modern-table :deep(.el-table__row) {
  height: 48px;
}
.modern-table :deep(.el-table__body tr:hover > td) {
  background-color: #F8FAFC !important;
}
.modern-table :deep(.el-table__body tr:nth-child(even) > td) {
  background-color: #FCFCFD;
}
.modern-table :deep(.el-table__footer td) {
  background-color: #F8FAFC !important;
  font-weight: 600;
  color: #1E293B;
}

.rule-scope-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.modern-table :deep(.rule-scope-table-column .cell) {
  overflow: visible;
}

.rule-scope-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: #475569;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rule-scope-cell.is-expanded {
  align-items: flex-start;
}

.rule-scope-cell.is-expanded .rule-scope-text {
  overflow: visible;
  line-height: 1.7;
  text-overflow: clip;
  white-space: normal;
  word-break: break-word;
}

.rule-scope-toggle {
  flex: 0 0 auto;
  border: 0;
  border-radius: 999px;
  background: #EEF2FF;
  color: #4F46E5;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  padding: 5px 9px;
  transition: background-color 0.18s ease, color 0.18s ease;
}

.rule-scope-toggle:hover,
.rule-scope-toggle:focus-visible {
  background: #E0E7FF;
  color: #3730A3;
  outline: none;
}

/* Text helpers */
.text-bold { font-weight: 600; color: #1E293B; }
.text-muted { color: #CBD5E1; }
.text-danger { color: #EF4444; font-weight: 600; }
.text-warning { color: #F59E0B; font-weight: 600; }

/* Clock time */
.clock-time { font-weight: 600; font-size: 14px; }
.clock-time--normal { color: #10B981; }
.clock-time--late { color: #EF4444; }
.clock-time--missing { color: #CBD5E1; font-weight: 400; }

.punch-time-record-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.punch-time-record-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #E8EEF6;
}

.punch-time-record-title h3 {
  margin: 0;
  color: #111827;
  font-size: 18px;
  font-weight: 700;
}

.punch-time-record-title span {
  color: #94A3B8;
  font-size: 13px;
}

.filter-row--punch-time {
  margin-bottom: 0;
}

.punch-time-calendar-table {
  border-radius: 8px;
  overflow: hidden;
}

.punch-time-calendar-table :deep(.el-table__header .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  color: #6B7280;
  white-space: pre-line;
  line-height: 1.35;
  text-align: center;
  word-break: keep-all;
}

.punch-time-calendar-table :deep(.el-table__body .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 58px;
  color: #1F2937;
  white-space: pre-line;
  text-align: center;
  word-break: keep-all;
}

.punch-time-employee {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  justify-content: center;
}

.punch-time-employee img,
.punch-time-employee__avatar {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  object-fit: cover;
  flex: 0 0 auto;
}

.punch-time-employee__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #EEF2FF;
  color: #4F46E5;
  font-size: 12px;
  font-weight: 700;
}

.punch-time-employee strong {
  color: #111827;
  font-weight: 600;
}

.punch-time-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  min-height: 50px;
  width: 100%;
  line-height: 1.35;
}

.punch-time-cell span {
  display: block;
}

.punch-time-cell__empty {
  color: #C0C7D2;
}

/* Work hours */
.work-hours { font-weight: 500; color: #475569; }

/* Location cell */
.location-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #64748B;
  font-size: 13px;
}
.location-cell--ok {
  color: #10B981;
}
.location-cell--warn {
  color: #F5222D;
}

.name-link {
  font-weight: 600;
  padding: 0;
}
.emp-no {
  color: #94A3B8;
  font-size: 12px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
}
.status-pill--normal { color: #52c41a; background: rgba(82, 196, 26, 0.12); }
.status-pill--late, .status-pill--early, .status-pill--multi, .status-pill--location { color: #fa8c16; background: rgba(250, 140, 22, 0.14); }
.status-pill--missed, .status-pill--absent { color: #f5222d; background: rgba(245, 34, 45, 0.12); }
.status-pill--abnormal { color: #f5222d; background: rgba(245, 34, 45, 0.12); }
.status-pill--leave { color: #1890ff; background: rgba(24, 144, 255, 0.12); }
.status-pill--trip { color: #722ed1; background: rgba(114, 46, 209, 0.12); }
.status-pill--appealing { color: #faad14; background: rgba(250, 173, 20, 0.15); }

:deep(.record-row--exception td) {
  background: #FFF4F4 !important;
}

/* Pagination */
.pagination-wrapper {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

/* Rule Cards Grid */
.rule-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}
.rule-card {
  background: #fff;
  border: 1px solid #E2E8F0;
  border-radius: 12px;
  padding: 20px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.rule-card:hover {
  border-color: #4F46E5;
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.08);
}
.rule-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #F1F5F9;
}
.rule-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rule-card__name {
  font-size: 15px;
  font-weight: 600;
  color: #1E293B;
}
.rule-card__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rule-card__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.rule-card__label {
  font-size: 13px;
  color: #94A3B8;
  flex-shrink: 0;
}
.rule-card__value-text {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 14px;
  font-weight: 500;
  color: #475569;
}
.check-methods {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.check-method-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  background: #F1F5F9;
  color: #475569;
}
.chip--gps { background: rgba(79,70,229,0.1); color: #4F46E5; }
.chip--wifi { background: rgba(16,185,129,0.1); color: #10B981; }
.chip--photo { background: rgba(245,158,11,0.1); color: #F59E0B; }
.chip--gate { background: rgba(99,102,241,0.1); color: #6366F1; }

/* Add Rule Card */
.rule-card--add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  border-style: dashed;
  border-color: #CBD5E1;
  cursor: pointer;
  transition: all 0.2s;
}
.rule-card--add:hover {
  border-color: #4F46E5;
  background: rgba(79, 70, 229, 0.02);
}
.rule-card--add__text {
  font-size: 14px;
  color: #94A3B8;
  margin-top: 8px;
}

/* Dialog */
.modern-dialog :deep(.el-dialog__header) {
  border-bottom: 1px solid #F1F5F9;
  padding: 16px 20px;
  margin-right: 0;
}
.modern-dialog :deep(.el-dialog__body) {
  padding: 20px;
}
.modern-dialog :deep(.el-dialog__footer) {
  border-top: 1px solid #F1F5F9;
  padding: 12px 20px;
}
.modern-dialog :deep(.el-dialog) {
  border-radius: 12px;
}
.wifi-dialog :deep(.el-dialog__body) {
  padding-top: 18px;
  padding-bottom: 12px;
}
.wifi-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding-bottom: 20px;
  border-bottom: 1px solid #E5E7EB;
}
.wifi-field-row {
  display: grid;
  grid-template-columns: 130px 1fr;
  column-gap: 10px;
  align-items: center;
}
.wifi-field-row--bssid {
  align-items: flex-start;
}
.wifi-field-label {
  color: #1F2937;
  font-size: 16px;
  font-weight: 500;
  line-height: 44px;
}
.wifi-ssid-input {
  max-width: 620px;
}
.wifi-bssid-group {
  min-height: 44px;
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
}
.wifi-bssid-input {
  width: 76px;
}
.wifi-bssid-input :deep(.el-input__wrapper) {
  padding: 0 6px;
}
.wifi-bssid-input :deep(.el-input__inner) {
  text-align: center;
  letter-spacing: 0.5px;
}
.wifi-bssid-colon {
  color: #1F2937;
  font-size: 22px;
  line-height: 1;
  margin: 0 8px;
}
.wifi-help {
  padding-top: 16px;
}
.wifi-help__title {
  font-size: 16px;
  font-weight: 600;
  color: #1F2937;
  margin-bottom: 14px;
}
.wifi-help__panel {
  background: #F3F4F6;
  border-radius: 8px;
  padding: 18px 18px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 1px 210px;
  align-items: stretch;
  column-gap: 18px;
}
.wifi-help__text {
  color: #374151;
  font-size: 16px;
  line-height: 22px;
}
.wifi-help__tip {
  margin-top: 6px;
  color: #6B7280;
}
.wifi-help__split {
  background: #D1D5DB;
}
.wifi-help__qrcode-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
}
.wifi-help__qrcode {
  width: 170px;
  height: 170px;
  border: 8px solid #fff;
  background:
    linear-gradient(90deg, #000 10%, transparent 10%) 0 0/20px 20px,
    linear-gradient(#000 10%, transparent 10%) 0 0/20px 20px,
    repeating-linear-gradient(45deg, #111 0 8px, #fff 8px 16px);
}
.wifi-dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.wifi-dialog-footer__links {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.wifi-dialog-footer__actions {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}
.wifi-dialog-footer__actions :deep(.el-button) {
  min-width: 104px;
}

:deep(.el-tag--light) {
  border: none;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid #F1F5F9;
}
.detail-row span {
  color: #64748B;
}
.detail-row strong {
  color: #1E293B;
  text-align: right;
}

.record-detail-actions {
  margin-top: 18px;
  padding: 14px;
  border: 1px solid #BFDBFE;
  border-radius: 8px;
  background: #EFF6FF;
}
.record-detail-actions__hint {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}
.record-detail-actions__hint strong {
  color: #1D4ED8;
  font-size: 15px;
}
.record-detail-actions__hint span {
  color: #64748B;
  line-height: 1.5;
}
.record-detail-actions__buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.clickable-table :deep(.el-table__row) {
  cursor: pointer;
}

.appeal-detail {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.appeal-detail__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 4px;
}

.appeal-detail__title {
  font-size: 18px;
  font-weight: 700;
  color: #1E293B;
}

.appeal-detail__sub {
  margin-top: 4px;
  font-size: 13px;
  color: #64748B;
}

.appeal-detail__section h3 {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 700;
  color: #1E293B;
}

.appeal-form-grid {
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  overflow: hidden;
}

.appeal-form-row {
  display: grid;
  grid-template-columns: 128px minmax(0, 1fr);
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid #F1F5F9;
}

.appeal-form-row:last-child {
  border-bottom: none;
}

.appeal-form-row span {
  color: #64748B;
}

.appeal-form-row strong {
  color: #1E293B;
  font-weight: 600;
  word-break: break-word;
}

.timeline-action {
  color: #1E293B;
  font-weight: 600;
}

.timeline-comment {
  margin-top: 4px;
  color: #64748B;
}

.filter-row--daily {
  gap: 10px;
  flex-wrap: wrap;
}

.summary-inline-loading {
  margin-top: 6px;
  margin-bottom: 8px;
  padding: 6px 0 2px;
  display: flex;
  align-items: center;
  gap: 12px;
  color: #64748B;
  font-size: 13px;
}

.summary-inline-loading__bar {
  position: relative;
  width: min(360px, 45vw);
  height: 6px;
  border-radius: 999px;
  background: #E5E7EB;
  overflow: hidden;
  flex-shrink: 0;
}

.summary-inline-loading__bar::after {
  content: '';
  position: absolute;
  inset: 0;
  width: 35%;
  border-radius: inherit;
  background: linear-gradient(90deg, #6366F1 0%, #22D3EE 100%);
  animation: summary-loading-slide 1.2s ease-in-out infinite;
}

@keyframes summary-loading-slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(300%);
  }
}

.summary-task-panel {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 42px;
  margin: 6px 0 8px;
  padding: 8px 12px;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  background: #F8FAFC;
  color: #334155;
}

.summary-task-panel--failed {
  border-color: #FECACA;
  background: #FEF2F2;
}

.summary-task-panel--succeeded {
  border-color: #BBF7D0;
  background: #F0FDF4;
}

.summary-task-panel__main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.summary-task-panel__message {
  min-width: 0;
  overflow: hidden;
  color: #475569;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-task-panel__cache {
  flex-shrink: 0;
  color: #16A34A;
  font-size: 12px;
  font-weight: 600;
}

.summary-task-panel__progress {
  width: 180px;
  flex-shrink: 0;
}

.report-main-tabs {
  margin-top: -4px;
}

.report-main-tabs :deep(.el-tabs__header) {
  margin: 0 0 20px;
}

.report-main-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
  background: #E8EEF6;
}

.report-main-tabs :deep(.el-tabs__item) {
  height: 44px;
  padding: 0 20px;
  color: #64748B;
  font-weight: 700;
}

.report-main-tabs :deep(.el-tabs__item.is-active) {
  color: #4F46E5;
}

.report-main-tabs :deep(.el-tabs__active-bar) {
  height: 3px;
  border-radius: 999px;
  background: #4F46E5;
}

.daily-report-subtabs {
  margin-top: 2px;
}

.daily-report-table {
  margin-top: 10px;
}

.daily-report-table :deep(.el-table__header .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  white-space: pre-line;
  line-height: 1.35;
  text-align: center;
  word-break: keep-all;
}

.daily-report-table :deep(.el-table__body .cell) {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 30px;
  white-space: normal;
  line-height: 1.45;
  text-align: center;
  word-break: keep-all;
}

.daily-report-table :deep(.el-table__cell) {
  vertical-align: middle;
}

.daily-setting-group + .daily-setting-group {
  margin-top: 14px;
}

.daily-setting-group__title {
  margin-bottom: 8px;
  font-weight: 600;
  color: #334155;
}

.daily-setting-hint {
  margin-bottom: 12px;
  color: #64748B;
  font-size: 13px;
}

.daily-setting-detail {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  row-gap: 8px;
}

.report-setting-drawer :deep(.el-drawer__header) {
  margin-bottom: 0;
  padding: 20px 24px 12px;
  border-bottom: 1px solid #E8EEF6;
  color: #111827;
  font-weight: 700;
}

.report-setting-drawer :deep(.el-drawer__body) {
  padding: 14px 24px 84px;
}

.report-setting-drawer :deep(.el-drawer__footer) {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 24px;
  background: #fff;
  border-top: 1px solid #E8EEF6;
}

.report-setting-subtitle {
  margin-bottom: 12px;
  color: #64748B;
  font-size: 13px;
}

.report-setting-tabs :deep(.el-tabs__header) {
  margin-bottom: 14px;
}

.report-setting-collapse {
  border-top: none;
  border-bottom: none;
}

.report-setting-collapse :deep(.el-collapse-item__header) {
  height: 44px;
  padding: 0 12px;
  border-radius: 8px;
  background: #F8FAFC;
  border-bottom: none;
}

.report-setting-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}

.report-setting-group-title {
  color: #334155;
  font-weight: 700;
}

.report-setting-group-count {
  margin-left: 8px;
  color: #94A3B8;
  font-size: 12px;
}

.report-field-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 0 14px;
}

.report-field-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 38px;
  padding: 0 10px;
  border: 1px solid #E8EEF6;
  border-radius: 8px;
  background: #fff;
  cursor: default;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
}

.report-field-row::before {
  position: absolute;
  top: -6px;
  right: 8px;
  left: 8px;
  height: 0;
  border-top: 2px dashed transparent;
  content: '';
  pointer-events: none;
}

.report-field-row::after {
  position: absolute;
  right: 8px;
  bottom: -6px;
  left: 8px;
  height: 0;
  border-top: 2px dashed transparent;
  content: '';
  pointer-events: none;
}

.report-field-row:hover {
  border-color: #C7D2FE;
  box-shadow: 0 6px 16px rgba(79, 70, 229, 0.08);
}

.report-field-row.is-dragging {
  opacity: 0.56;
  border-style: dashed;
}

.report-field-row.is-drop-before::before,
.report-field-row.is-drop-after::after {
  border-top-color: #4F46E5;
}

.report-field-row.is-drop-target {
  border-color: #C7D2FE;
  background: #F8FAFF;
}

.report-field-row__drag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 26px;
  width: 26px;
  height: 26px;
  margin-left: auto;
  border-radius: 7px;
  background: #F1F5F9;
  color: #A8B3C4;
  font-size: 14px;
  line-height: 1;
  cursor: grab;
}

.report-field-row__drag:active {
  cursor: grabbing;
}

.report-field-row :deep(.el-checkbox) {
  flex: 1;
  min-width: 0;
  height: 38px;
}

.report-field-row :deep(.el-checkbox__label) {
  color: #334155;
  font-weight: 500;
}

@media (max-width: 1100px) {
  .trend-cards {
    grid-template-columns: 1fr;
  }
  .rule-form-grid {
    grid-template-columns: 1fr;
  }
  .rule-type-cards {
    grid-template-columns: 1fr;
  }
  .special-date-inline {
    grid-template-columns: 1fr;
  }
  .shift-template-builder {
    grid-template-columns: 1fr;
  }
  .popup-form-row--3,
  .popup-form-row--2 {
    grid-template-columns: 1fr;
  }
  .special-time-line {
    flex-wrap: wrap;
  }
  .segment-summary-row {
    flex-direction: column;
  }
  .segment-summary-row__actions {
    text-align: left;
  }
  .daily-setting-detail {
    grid-template-columns: 1fr 1fr;
  }
  .overtime-setting-row,
  .overtime-rule-line {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .overtime-setting-row__label,
  .overtime-rule-line__label {
    line-height: 1.5;
  }
  .overtime-method-card {
    width: 100%;
  }
  .overtime-rule-tabs {
    gap: 16px;
    overflow: auto;
    white-space: nowrap;
  }
  .overtime-rule-tab {
    font-size: 24px;
  }
  .time-table-wrap {
    margin-left: 0;
    width: 100%;
  }
  .time-table__head {
    grid-template-columns: 1fr 1fr;
  }
  .time-table__row {
    grid-template-columns: 1fr;
  }
  .time-table__actions {
    justify-content: flex-start;
    border-top: 1px dashed #E5E7EB;
  }
  .segment-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .segment-label {
    line-height: 1.4;
  }
  .segment-time-line {
    grid-template-columns: 1fr;
  }
  .segment-time-line--rest {
    grid-template-columns: 1fr;
  }
  .rest-time-row {
    grid-template-columns: 1fr;
  }
  .punch-window-row {
    grid-template-columns: 1fr;
  }
  .punch-window-row__hint {
    margin: -4px 0 8px 0;
  }
  .punch-window-row__remove {
    position: static;
    margin-top: -6px;
    margin-bottom: 6px;
  }
  .segment-footer {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  .holiday-desc,
  .restday-desc {
    display: block;
    margin-left: 0;
    margin-top: 6px;
  }
  .punch-choice-item {
    grid-template-columns: 1fr;
    row-gap: 4px;
  }
  .wifi-field-row {
    grid-template-columns: 1fr;
    row-gap: 8px;
  }
  .wifi-help__panel {
    grid-template-columns: 1fr;
    row-gap: 12px;
  }
  .wifi-help__split {
    display: none;
  }
  .wifi-dialog-footer {
    flex-direction: column;
    gap: 10px;
    align-items: flex-start;
  }
}

@media (max-width: 768px) {
  .attendance-page {
    padding: 12px;
  }

  .stat-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }

  .stat-card {
    min-width: 0;
    padding: 16px;
    align-items: flex-start;
  }

  .stat-card__icon {
    width: 44px;
    height: 44px;
  }

  .stat-card__info {
    min-width: 0;
  }

  .stat-card__value {
    flex-wrap: wrap;
  }

  .stat-card__num {
    font-size: 24px;
  }

  .stat-card__label {
    line-height: 1.4;
    word-break: break-word;
  }

  .trend-cards {
    gap: 10px;
    margin-bottom: 12px;
  }

  .content-card {
    padding: 4px 14px 14px;
  }

  .quick-filter-row {
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: 8px !important;
  }

  .quick-filter-row .el-tag {
    display: flex;
    justify-content: center;
  }

  .filter-row {
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: 8px !important;
    align-items: stretch !important;
  }

  .filter-row > .el-date-editor {
    grid-column: 1 / -1;
  }

  .filter-row .el-dropdown {
    width: 100%;
  }

  .filter-row .el-dropdown .el-button {
    width: 100%;
  }

  .batch-toolbar {
    flex-wrap: wrap;
    gap: 8px;
  }
}

@media (max-width: 560px) {
  .attendance-page {
    padding: 10px;
  }
}

.attendance-page--landscape-compact {
  padding: 12px;
}

.attendance-page--landscape-compact .page-header {
  margin-bottom: 14px;
}

.attendance-page--landscape-compact .stat-cards {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.attendance-page--landscape-compact .stat-card {
  padding: 12px;
  gap: 10px;
}

.attendance-page--landscape-compact .stat-card__icon {
  width: 40px;
  height: 40px;
}

.attendance-page--landscape-compact .stat-card__num {
  font-size: 22px;
}

.attendance-page--landscape-compact .trend-cards {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.attendance-page--landscape-compact .content-card {
  padding: 4px 16px 16px;
}

.attendance-page--landscape-compact .quick-filter-row {
  flex-wrap: wrap;
  gap: 6px;
}

.attendance-page--landscape-compact .filter-row {
  gap: 8px;
  margin-bottom: 12px;
}

.attendance-page--landscape-compact .batch-toolbar {
  gap: 8px;
}
</style>
