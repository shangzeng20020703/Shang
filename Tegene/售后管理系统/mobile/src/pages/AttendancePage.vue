<template>
  <ion-page>
    <ion-content fullscreen class="app-gradient-page">
      <div class="mobile-shell attendance-shell">
        <header class="attendance-native-head">
          <button type="button" class="head-icon-btn" aria-label="返回" @click="goBack">‹</button>
          <h1>
            <span>{{ attendanceTitle }}</span>
            <small v-if="attendanceSubtitle">{{ attendanceSubtitle }}</small>
          </h1>
          <button type="button" class="head-icon-btn" aria-label="更多" @click="handleHeaderAction">{{ headerRightIcon }}</button>
        </header>

        <template v-if="activeMainTab === 'punch'">
          <section class="section-block solid-card punch-panel">
            <div class="project-punch-context" v-if="runtime">
              <strong>{{ runtime.project ? runtime.project.name : '当前未关联项目' }}</strong>
              <span>{{ runtime.project ? `按项目规则打卡 · ${runtime.rule?.name || '规则加载中'}` : (runtime.has_rule ? `当前按个人规则：${runtime.rule?.name}` : '请联系负责人分配项目') }}</span>
            </div>
            <details v-if="runtime?.project_segments?.length" class="project-punch-context">
              <summary>今日项目记录 · {{runtime.project_segments.length}} 段</summary>
              <div v-for="part in runtime.project_segments" :key="part.segment_id">
                <strong>{{part.project_name}} · {{part.rule_name}}</strong>
                <span>上班 {{formatClockOnly(part.clock_in_time)}} / 下班 {{formatClockOnly(part.clock_out_time)}}<b v-if="part.needs_review"> · 待核对</b></span>
              </div>
            </details>
            <div class="subtab-row">
              <button
                type="button"
                class="subtab-pill"
                :class="{ 'is-active': punchScene === 'normal' }"
                @click="punchScene = 'normal'"
              >
                上下班打卡
              </button>
              <button
                type="button"
                class="subtab-pill"
                :class="{ 'is-active': punchScene === 'outside' }"
                @click="punchScene = 'outside'"
              >
                外出打卡
              </button>
            </div>

            <div class="map-surface" :class="{ 'is-expanded': mapExpanded }">
              <div ref="mapContainerRef" class="tencent-map-container" />
              <img v-if="!mapReady && mapPreview" class="map-image" :src="mapPreview" alt="腾讯地图打卡定位" />
              <div v-if="!mapReady" class="map-loading-state">
                <div class="map-avatar-dot is-loading">工</div>
                <span>{{ mapLoadingText }}</span>
              </div>
              <div v-if="mapReady" class="map-compact-compass" aria-hidden="true">
                <span>N</span>
              </div>
              <div v-if="mapReady" class="map-legend">
                <span><i class="map-legend-dot is-current"></i>我的位置</span>
                <span><i class="map-legend-dot is-target"></i>打卡点</span>
              </div>
              <button
                v-if="mapReady"
                type="button"
                class="map-expand-btn"
                :aria-label="mapExpanded ? '收起地图' : '放大地图'"
                @mousedown.stop
                @touchstart.stop
                @click.stop.prevent="toggleMapExpanded"
              >
                <span class="map-expand-icon" :class="{ 'is-expanded': mapExpanded }" aria-hidden="true">
                  <i></i>
                  <i></i>
                  <i></i>
                  <i></i>
                </span>
              </button>
            </div>

            <button
              v-if="punchScene === 'outside'"
              type="button"
              class="range-status-picker"
              @click="openOutsideAddressPicker"
            >
              <span class="range-status-main">{{ geofenceMainText }}</span>
              <span class="range-status-sub">{{ geofenceSubText }}</span>
            </button>
            <template v-else>
              <div class="range-status-main" :class="{ 'is-warning': isInsideGeofence === false }">
                {{ geofenceMainText }}
              </div>
              <div class="range-status-sub">{{ geofenceSubText }}</div>
            </template>

            <template v-if="punchScene === 'normal'">
              <button
                type="button"
                class="punch-circle-btn"
                :class="[
                  `is-${punchUiState.tone}`,
                  {
                    'is-submitting': punchSubmitting,
                    'is-clock-out': punchPhase === 'clock_out',
                  },
                ]"
                :disabled="punchDisabled || punchSubmitting"
                @click="handleMainPunch"
              >
                <span class="punch-circle-btn__title">{{ punchCircleTitle }}</span>
                <span class="punch-circle-btn__time">{{ cstClockText }}</span>
                <span v-if="punchCircleHint" class="punch-circle-btn__hint">{{ punchCircleHint }}</span>
              </button>
              <div v-if="runtime?.rule?.require_photo" class="outside-form-list">
                <button type="button" class="outside-form-row" @click="outsidePhotoInputRef?.click()">
                  <span class="outside-form-label">打卡图片</span>
                  <span class="outside-placeholder">
                    <img v-if="outsidePhotoPreview" class="outside-photo-thumb" :src="outsidePhotoPreview" alt="打卡照片" @click.stop="photoPreviewVisible = true" />
                    <span>{{ outsidePhotoReady ? '重新拍照' : '拍摄打卡照片' }}</span>
                  </span>
                  <span class="outside-row-icon">▧</span>
                </button>
                <input ref="outsidePhotoInputRef" class="outside-photo-input" type="file" accept="image/*" capture="environment" @change="handleOutsidePhotoChange" />
              </div>
              <button type="button" class="punch-rule-line" @click="openAttendanceStatsFromRule">
                <span>上班 {{ expectedInText }}</span>
                <span v-if="clockInRuleAlertVisible" class="rule-alert-dot">!</span>
                <span v-else class="green-dot" :class="ruleLineMarkerClass" />
                <span>下班 {{ expectedOutText }}</span>
                <span v-if="clockOutRuleAlertVisible" class="rule-alert-dot">!</span>
                <span v-else class="green-dot" :class="ruleLineMarkerClass" />
                <span class="chevron">›</span>
              </button>
            </template>

            <template v-else>
              <div class="outside-form-list">
                <label class="outside-form-row">
                  <span class="outside-form-label">拜访客户</span>
                  <ion-input v-model="outsideClient" class="outside-inline-input" placeholder="添加拜访客户" />
                  <span class="outside-row-icon">♙</span>
                </label>
                <button type="button" class="outside-form-row" @click="markOutsidePhoto">
                  <span class="outside-form-label">打卡图片</span>
                  <span class="outside-placeholder">
                    <img v-if="outsidePhotoPreview" class="outside-photo-thumb" :src="outsidePhotoPreview" alt="打卡图片" @click.stop="photoPreviewVisible = true" />
                    <span>{{ outsidePhotoStatusText }}</span>
                  </span>
                  <span class="outside-row-icon">▧</span>
                </button>
                <input
                  ref="outsidePhotoInputRef"
                  class="outside-photo-input"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  @change="handleOutsidePhotoChange"
                />
                <label class="outside-form-row">
                  <span class="outside-form-label">打卡备注</span>
                  <ion-input v-model="outsideRemark" class="outside-inline-input" placeholder="填写拜访记录" />
                </label>
              </div>

              <button
                type="button"
                class="punch-circle-btn is-outside"
                :disabled="punchSubmitting"
                @click="handleOutsidePunch"
              >
                <span class="punch-circle-btn__title">外出打卡</span>
                <span class="punch-circle-btn__time">{{ cstClockText }}</span>
              </button>

              <button
                type="button"
                class="outside-approval-card"
                :disabled="punchSubmitting"
                @click="submitOutsideApproval"
              >
                <span>
                  <strong>外出打卡需提交审批</strong>
                  <small>提交后由上级、审批员确认后可补正打卡</small>
                </span>
                <em>提交审批</em>
              </button>
            </template>
          </section>

          <button
            v-if="todayHasAbnormal"
            type="button"
            class="section-block abnormal-entry-card"
            @click="openTodayAbnormal"
          >
            <span class="abnormal-entry-card__left">
              <span class="abnormal-entry-card__icon">☰</span>
              <strong>本月待处理异常</strong>
            </span>
            <span class="abnormal-entry-card__meta">{{ monthAbnormalDaysText }}</span>
            <span class="chevron">›</span>
          </button>

          <section class="section-block solid-card today-record-card">
            <h3 class="record-title">今日打卡记录</h3>
            <div class="today-record-grid">
              <div class="today-record-cell">
                <span class="today-record-label">状态</span>
                <strong class="today-record-value" :class="{ 'is-abnormal-text': todayHasAbnormal }">
                  <span v-if="todayHasAbnormal" class="inline-warning-mark">!</span>
                  <span v-else class="green-dot inline-ok-mark" />
                  {{ todayStatusDisplayText }}
                </strong>
              </div>
              <div class="today-record-cell"><span class="today-record-label">上班</span><strong class="today-record-value">{{ todayClockInShortText }}</strong></div>
              <div class="today-record-cell"><span class="today-record-label">下班</span><strong class="today-record-value">{{ todayClockOutShortText }}</strong></div>
              <div class="today-record-cell"><span class="today-record-label">异常</span><strong class="today-record-value">{{ todayAbnormalText }}</strong></div>
            </div>
          </section>
        </template>

        <template v-else-if="activeMainTab === 'apply'">
          <section class="approval-template-page attendance-approval-start" aria-label="发起申请">
            <label class="approval-template-search">
              <span aria-hidden="true">⌕</span>
              <input v-model="attendanceTemplateSearch" type="search" placeholder="搜索模板名称" />
            </label>

            <section v-if="attendanceTemplateLoading" class="approval-template-empty">
              <strong>正在加载可用申请模板</strong>
              <span>请稍候。</span>
            </section>

            <section v-else class="approval-template-sections">
              <article
                v-for="group in visibleAttendanceTemplateGroups"
                :key="group.category"
                class="approval-template-section"
              >
                <h2>{{ group.category }}</h2>
                <div class="approval-template-grid">
                  <template v-for="slot in attendanceTemplateGridSlots(group.templates)" :key="slot.key">
                    <span
                      v-if="slot.placeholder"
                      class="approval-template-item approval-template-item--placeholder"
                      aria-hidden="true"
                    ></span>
                    <button
                      v-else
                      type="button"
                      class="approval-template-item"
                      :aria-label="slot.template.name"
                      @click="openApplyItem(slot.template)"
                    >
                      <span class="approval-template-icon" :class="`approval-template-icon--${slot.template.color}`">
                        <ion-icon :icon="slot.template.icon" />
                      </span>
                      <span class="approval-template-name">{{ slot.template.name }}</span>
                    </button>
                  </template>
                </div>
              </article>
              <section v-if="!visibleAttendanceTemplateGroups.length" class="approval-template-empty">
                <strong>暂时没有匹配模板</strong>
                <span>换个关键词再试试。</span>
              </section>
            </section>
          </section>
          <button type="button" class="section-block apply-record-row" @click="router.push('/app/tabs/approvals?segment=submitted')">
            <span class="apply-record-left"><span class="apply-record-icon">◷</span>申请记录</span>
            <span class="apply-record-count">{{ applicationRecordCountText }}</span>
          </button>
        </template>

        <template v-else-if="activeMainTab === 'stats'">
          <section class="section-block stats-mobile-head">
            <div class="stats-year-line">{{ statsYearText }}</div>
            <div class="stats-head-row">
              <button v-if="statsRange !== 'day'" type="button" class="stats-arrow" @click="shiftStatsPeriod(-1)">‹</button>
              <div class="stats-period-title">{{ statsPeriodTitle }}</div>
              <div class="stats-toggle-row">
                <button
                  v-for="item in statsRangeOptions"
                  :key="item.value"
                  type="button"
                  class="stats-range-pill"
                  :class="{ 'is-active': statsRange === item.value }"
                  @click="statsRange = item.value"
                >
                  {{ item.label }}
                </button>
              </div>
            </div>
            <div v-if="statsRange === 'day'" class="stats-week-strip">
              <button
                v-for="day in statsDayStrip"
                :key="day.date"
                type="button"
                class="stats-day-cell"
                :class="{ 'is-active': day.date === statsDate }"
                @click="statsDate = day.date"
              >
                <span>{{ day.week }}</span>
                <strong>{{ day.day }}</strong>
              </button>
            </div>
          </section>

          <section class="section-block stats-export-row">
            <button type="button" @click="openExportReportPage">
              <span>导出报表</span>
            </button>
            <button type="button" @click="openAttendanceSummaryFromStats()">
              <span>▦ 我的考勤汇总 ›</span>
            </button>
          </section>

          <section v-if="statsRange === 'day'" class="section-block solid-card stats-day-card">
            <div class="stats-card-head">
              <button type="button" class="stats-rule-title-btn" @click="openRuleDetail">
                <strong>{{ dailyCardTitle }}</strong>
                <span>打卡规则：{{ ruleDisplayName }}</span>
              </button>
              <button type="button" class="plain-chevron-btn" @click="openRuleDetail">›</button>
            </div>
            <div class="day-time-line">
              <div
                class="day-time-row"
                :class="{ 'is-clickable': dailyClockInIsAbnormal }"
                :role="dailyClockInIsAbnormal ? 'button' : undefined"
                :tabindex="dailyClockInIsAbnormal ? 0 : undefined"
                @click="dailyClockInIsAbnormal && openPunchDetailFromStats('check_in')"
                @keydown.enter="dailyClockInIsAbnormal && openPunchDetailFromStats('check_in')"
                @keydown.space.prevent="dailyClockInIsAbnormal && openPunchDetailFromStats('check_in')"
              >
                <strong>{{ dailyScheduledInText }}</strong>
                <span :class="{ 'is-abnormal-clock': dailyClockInIsAbnormal }">上班</span>
                <small :class="{ 'is-abnormal-clock': dailyClockInIsAbnormal }">{{ dailyClockInStatusLine }}</small>
                <button
                  v-if="dailyClockInIsAbnormal"
                  type="button"
                  class="inline-process-btn"
                  @click.stop="openPunchDetailFromStats('check_in')"
                >
                  处理异常
                </button>
              </div>
              <i />
              <div
                class="day-time-row"
                :class="{ 'is-clickable': dailyClockOutIsAbnormal }"
                :role="dailyClockOutIsAbnormal ? 'button' : undefined"
                :tabindex="dailyClockOutIsAbnormal ? 0 : undefined"
                @click="dailyClockOutIsAbnormal && openPunchDetailFromStats('check_out')"
                @keydown.enter="dailyClockOutIsAbnormal && openPunchDetailFromStats('check_out')"
                @keydown.space.prevent="dailyClockOutIsAbnormal && openPunchDetailFromStats('check_out')"
              >
                <strong :class="{ 'is-muted-clock': dailyClockOutMissing }">{{ dailyScheduledOutText }}</strong>
                <span :class="{ 'is-abnormal-clock': dailyClockOutIsAbnormal }">下班</span>
                <small :class="{ 'is-abnormal-clock': dailyClockOutIsAbnormal }">{{ dailyClockOutStatusLine }}</small>
                <button
                  v-if="dailyClockOutIsAbnormal"
                  type="button"
                  class="inline-process-btn"
                  @click.stop="openPunchDetailFromStats('check_out')"
                >
                  处理异常
                </button>
              </div>
            </div>
            <p class="stats-help-line">如有加班，可在申请页提交对应审批。</p>
          </section>

          <template v-else>
            <section class="section-block solid-card stats-summary-card">
              <div class="stats-card-head">
                <button type="button" class="stats-card-title-btn" @click="openAttendanceSummaryFromStats('attendance', 'all')">
                  <strong>上下班打卡</strong>
                </button>
                <button type="button" class="plain-chevron-btn" @click="openAttendanceSummaryFromStats('attendance', 'all')">›</button>
              </div>
              <div class="stats-gauge" :style="{ '--abnormal': `${gaugeAbnormalPercent}%` }">
                <button type="button" class="gauge-legend normal" @click="openAttendanceSummaryFromStats('attendance', 'normal')">
                  正常天数 {{ dashboardStats.normal_days }}
                </button>
                <button type="button" class="gauge-legend abnormal" @click="openAttendanceSummaryFromStats('attendance', 'abnormal')">
                  异常天数 {{ dashboardStats.abnormal_days }}
                </button>
              </div>
              <div class="stats-metric-row">
                <button type="button" @click="openAttendanceSummaryFromStats('attendance', 'late')"><strong>{{ dashboardStats.late_count }}</strong><span>迟到 /次</span></button>
                <button type="button" @click="openAttendanceSummaryFromStats('attendance', 'early_leave')"><strong>{{ dashboardStats.early_count }}</strong><span>早退 /次</span></button>
                <button type="button" @click="openAttendanceSummaryFromStats('attendance', 'missed_clock')"><strong>{{ dashboardStats.missed_count }}</strong><span>缺卡 /次</span></button>
                <button type="button" @click="openAttendanceSummaryFromStats('attendance', 'absent')"><strong>{{ dashboardStats.absent_count }}</strong><span>旷工 /次</span></button>
                <button type="button" @click="openAttendanceSummaryFromStats('attendance', 'abnormal')"><strong>{{ dashboardStats.other_abnormal_count }}</strong><span>其他异常 /次</span></button>
              </div>
              <div class="stats-summary-divider" />
              <div class="stats-card-head">
                <button type="button" class="stats-card-title-btn" @click="openAttendanceSummaryFromStats('leave', 'all')">
                  <strong>假勤 /次</strong>
                </button>
                <button type="button" class="plain-chevron-btn" @click="openAttendanceSummaryFromStats('leave', 'all')">›</button>
              </div>
              <div class="stats-leave-row">
                <button type="button" @click="openAttendanceSummaryFromStats('leave', 'leave')">请假 {{ dashboardStats.leave_count }}</button>
                <button type="button" @click="openAttendanceSummaryFromStats('leave', 'punch_correction')">补卡 {{ dashboardStats.correction_count }}</button>
                <button type="button" @click="openAttendanceSummaryFromStats('leave', 'outside')">外出 {{ dashboardStats.outside_count }}</button>
                <button type="button" @click="openAttendanceSummaryFromStats('leave', 'business_trip')">出差 {{ dashboardStats.business_trip_count }}</button>
                <button type="button" @click="openAttendanceSummaryFromStats('leave', 'field_work')">外勤 {{ dashboardStats.field_work_count }}</button>
              </div>
            </section>
            <button type="button" class="section-block stats-total-row" @click="openAttendanceSummaryFromStats('overtime', undefined, 'all', 'comp_time')">
              <strong>加班 /小时</strong>
              <span>{{ dashboardOvertimeHoursText }}</span>
            </button>
            <button type="button" class="section-block stats-total-row" @click="openAttendanceSummaryFromStats('leave', 'outside')">
              <strong>外出卡 /次</strong>
              <span>{{ dashboardStats.outside_count }}</span>
            </button>
            <section class="section-block solid-card stats-record-list-card">
              <button type="button" class="calibration-filter-trigger" @click="statusFilterVisible = true">
                校准状态：{{ statsFilterDisplayLabel }}⌄
              </button>
              <div v-if="filteredStatsRecords.length" class="monthly-detail-list">
                <div
                  v-for="record in filteredStatsRecords"
                  :key="`${record.id || record.date}-${record.clock_in_time || ''}`"
                  class="monthly-detail-row"
                  :class="{ 'is-abnormal': isRecordAbnormal(record) }"
                >
                  <button type="button" class="monthly-detail-row-main" @click="openPunchDetailFromRecord(record)">
                    {{ formatMonthDay(record.date) }} - {{ recordDisplayStatus(record) }}
                  </button>
                  <button
                    v-if="isRecordAbnormal(record)"
                    type="button"
                    class="monthly-detail-action-btn"
                    @click="openPunchDetailFromRecord(record)"
                  >
                    处理异常
                  </button>
                  <em v-else>详情</em>
                </div>
              </div>
              <div v-else class="stats-empty-state">当前筛选下暂无考勤记录</div>
            </section>
          </template>
        </template>

        <template v-else-if="activeMainTab === 'rule_detail'">
          <section class="rule-profile-card">
            <div class="rule-avatar">{{ ruleOwnerInitial }}</div>
            <div>
              <strong>{{ ruleOwnerName }}</strong>
              <span>打卡规则：{{ ruleDisplayName }}</span>
            </div>
          </section>

          <section class="rule-section-card">
            <button type="button" class="rule-section-head" @click="toggleRuleSection('time')">
              <strong>打卡时间</strong>
              <span>{{ openedRuleSections.time ? '⌃' : '⌄' }}</span>
            </button>
            <div v-if="openedRuleSections.time" class="rule-section-body">
              <p class="rule-label">上下班时间</p>
              <p>周一至周五</p>
              <p>{{ ruleTimeDescription }}</p>
              <p>(GMT+08:00, 中国标准时间 - 北京)</p>
            </div>
          </section>

          <section class="rule-section-card">
            <button type="button" class="rule-section-head" @click="toggleRuleSection('range')">
              <strong>打卡范围</strong>
              <span>{{ openedRuleSections.range ? '⌃' : '⌄' }}</span>
            </button>
            <div v-if="openedRuleSections.range" class="rule-section-body">
              <p class="rule-label">打卡地点</p>
              <p><em class="location-tag">⌖ {{ ruleLocationName }}</em></p>
              <p class="rule-label">打卡Wi-Fi</p>
              <p>{{ ruleWifiName }}</p>
            </div>
          </section>

          <section class="rule-section-card">
            <button type="button" class="rule-section-head" @click="toggleRuleSection('overtime')">
              <strong>加班规则</strong>
              <span>{{ openedRuleSections.overtime ? '⌃' : '⌄' }}</span>
            </button>
            <div v-if="openedRuleSections.overtime" class="rule-section-body">
              <p>工作日：允许加班</p>
              <p class="muted-rule-text">计算方式：按打卡时长计算</p>
              <p class="muted-rule-text">加班时长计为：调休</p>
              <p>休息日：允许加班</p>
              <p class="muted-rule-text">计算方式：按打卡时长计算</p>
              <p class="muted-rule-text">加班时长计为：调休</p>
              <p>节假日：允许加班</p>
              <p class="muted-rule-text">计算方式：按加班审批时长计算</p>
            </div>
          </section>

          <section class="rule-section-card">
            <button type="button" class="rule-section-head" @click="toggleRuleSection('correction')">
              <strong>请假打卡</strong>
              <span>{{ openedRuleSections.correction ? '⌃' : '⌄' }}</span>
            </button>
            <div v-if="openedRuleSections.correction" class="rule-section-body">
              <p class="rule-label">请假打卡</p>
              <p>非全天请假、离岗返岗无需打卡</p>
            </div>
          </section>

          <section class="rule-section-card">
            <button type="button" class="rule-section-head" @click="toggleRuleSection('more')">
              <strong>更多规则</strong>
              <span>{{ openedRuleSections.more ? '⌃' : '⌄' }}</span>
            </button>
            <div v-if="openedRuleSections.more" class="rule-section-body">
              <p>允许范围外打卡，记录为地点异常。</p>
              <p>开启外出打卡记录同步。</p>
            </div>
          </section>
        </template>

        <template v-else-if="activeMainTab === 'month_detail'">
          <section class="month-detail-tabs">
            <button
              v-for="item in monthDetailTabs"
              :key="item.value"
              type="button"
              :class="{ 'is-active': monthDetailTab === item.value }"
              @click="monthDetailTab = item.value"
            >
              {{ item.label }}
            </button>
          </section>

          <section v-if="monthDetailTab === 'attendance'" class="month-detail-panel">
            <button type="button" class="calibration-filter-trigger month-filter" @click="statusFilterVisible = true">
              校准状态：{{ statsFilterDisplayLabel }}⌄
            </button>
            <div class="monthly-detail-list wecom-list">
              <div
                v-for="record in monthDetailRecords"
                :key="`${record.id || record.date}-${record.clock_in_time || ''}`"
                class="monthly-detail-row"
                :class="{ 'is-abnormal': isRecordAbnormal(record) }"
              >
                <button type="button" class="monthly-detail-row-main" @click="openPunchDetailFromRecord(record)">
                  {{ formatMonthDay(record.date) }} - {{ recordDisplayStatus(record) }}
                </button>
                <button v-if="isRecordAbnormal(record)" type="button" class="process-btn" @click="openPunchDetailFromRecord(record)">处理异常</button>
              </div>
              <div v-if="!monthDetailRecords.length" class="stats-empty-state">当前筛选下暂无考勤记录</div>
            </div>
          </section>

          <section v-else-if="monthDetailTab === 'leave'" class="month-detail-panel">
            <button type="button" class="calibration-filter-trigger month-filter" @click="leaveFilterVisible = true">
              假勤类型：{{ leaveFilterDisplayLabel }}⌄
            </button>
            <div v-if="filteredLeaveRows.length" class="monthly-detail-list wecom-list">
              <button
                v-for="item in filteredLeaveRows"
                :key="item.id"
                type="button"
                class="monthly-detail-row leave-detail-row"
                @click="openLeaveDetailItem(item)"
              >
                <span>{{ formatMonthDay(item.date) }} - {{ item.label }}</span>
                <small>{{ leaveRowMeta(item) }}</small>
              </button>
            </div>
            <div v-else class="empty-month-panel">无筛选结果</div>
          </section>

          <section v-else class="month-detail-panel overtime-detail-panel">
            <div class="overtime-table-head overtime-table-head--daily">
              <span>日期</span>
              <span>{{ overtimeHoursColumnLabel }}</span>
              <span>来源记录</span>
            </div>
            <div v-for="item in filteredOvertimeRows" :key="item.id" class="overtime-table-row overtime-table-row--daily">
              <span>
                <strong>{{ item.date }}</strong>
                <small>{{ item.dateType }}</small>
              </span>
              <span>{{ item.settlementAmountText }}</span>
              <span>
                <strong>{{ item.sourceText }}</strong>
                <small v-if="item.timeText">{{ item.timeText }}</small>
              </span>
            </div>
            <div v-if="!filteredOvertimeRows.length" class="empty-month-panel">暂无明细</div>
          </section>

          <button
            type="button"
            class="month-confirm-btn"
            :disabled="monthlyConfirmSubmitting"
            @click="confirmMonthlyAttendance"
          >
            {{ monthlyConfirmSubmitting ? '确认中...' : '确认 考勤结果无误' }}
          </button>
        </template>

        <template v-else-if="activeMainTab === 'export_report'">
          <section class="section-block export-report-card">
            <button type="button" class="export-option-row" @click="exportRangePickerVisible = true">
              <span>时间范围</span>
              <strong>{{ exportRangeLabel }} ›</strong>
            </button>
            <button type="button" class="export-option-row" @click="exportStatusPickerVisible = true">
              <span>打卡状态</span>
              <strong>{{ exportStatusLabel }} ›</strong>
            </button>
          </section>
          <button type="button" class="export-submit-btn" :disabled="statsExporting" @click="downloadCurrentStats">
            {{ statsExporting ? '正在导出...' : '导出报表' }}
          </button>
          <p class="export-help-text">将导出{{ exportOwnerName }}的个人上下班月报</p>
        </template>

        <template v-else-if="activeMainTab === 'punch_detail'">
          <section class="section-block solid-card punch-detail-card">
            <div class="punch-detail-status" :class="{ 'is-abnormal': punchDetailIsAbnormal }">
              <span>{{ punchDetailIsAbnormal ? '!' : '✓' }}</span>
              <strong>{{ punchDetailStatusTitle }}</strong>
              <small>{{ punchDetailStatusSubtitle }}</small>
            </div>
            <div class="detail-row-list">
              <div class="detail-row">
                <span>时间</span>
                <strong :class="{ 'is-danger-text': punchDetailIsAbnormal }">{{ punchDetailTimeText }}</strong>
              </div>
              <div class="detail-row detail-row--map">
                <span>位置</span>
                <strong>{{ punchDetailLocationTitle }}</strong>
                <small>{{ punchDetailLocationSub }}</small>
                <img v-if="mapPreview" :src="mapPreview" alt="打卡地点地图" />
              </div>
              <div v-if="punchDetailIsAbnormal" class="detail-row">
                <span>打卡设备</span>
                <strong>{{ punchDetailDeviceText }}</strong>
              </div>
              <div v-if="punchDetailIsAbnormal" class="detail-row">
                <span>备注</span>
                <strong>{{ punchDetailRemark }}</strong>
              </div>
            </div>
            <button type="button" class="punch-rule-line punch-detail-rule" @click="openAttendanceStatsFromRule">
              <span>上班 {{ expectedInText }}</span>
              <span v-if="punchDetailType === 'check_in' && punchDetailIsAbnormal" class="rule-alert-dot">!</span>
              <span v-else-if="punchDetailType === 'check_in'" class="green-dot" />
              <span>下班 {{ expectedOutText }}</span>
              <span v-if="punchDetailType === 'check_out' && punchDetailIsAbnormal" class="rule-alert-dot">!</span>
              <span v-else-if="punchDetailType === 'check_out'" class="green-dot" />
              <span class="chevron">›</span>
            </button>
            <div v-if="punchDetailIsAbnormal" class="punch-detail-actions">
              <button type="button" @click="openPunchCorrectionFromDetail">提交补卡申请</button>
              <button type="button" @click="openLeaveApplicationFromDetail">假勤申请</button>
            </div>
          </section>
        </template>

        <template v-else-if="activeMainTab === 'clock_out_result'">
          <section class="section-block solid-card clock-out-result-card">
            <div class="subtab-row clock-out-result-tabs">
              <button type="button" class="subtab-pill is-active" @click="activeMainTab = 'punch'">上下班打卡</button>
              <button type="button" class="subtab-pill" @click="openOutsidePunchFromResult">外出打卡</button>
            </div>

            <div class="clock-out-result-content">
              <div class="clock-out-result-icon" :class="{ 'is-normal': !clockOutResultHasAbnormal }">⌁</div>
              <h2>{{ clockOutResultTitle }}</h2>
              <p>今日打卡已完成</p>

              <div class="clock-out-result-rows">
                <div>
                  <span>时间：</span>
                  <strong :class="{ 'is-danger-text': clockOutResultHasAbnormal }">{{ clockOutResultTime }}</strong>
                </div>
                <div>
                  <span>位置：</span>
                  <strong>{{ clockOutResultLocation }}</strong>
                </div>
              </div>

              <div class="clock-out-result-actions">
                <button type="button" @click="openClockOutUpdatePage">更新下班卡</button>
              </div>

              <button type="button" class="punch-rule-line clock-out-result-rule" @click="openAttendanceStatsFromRule">
                <span>上班 {{ expectedInText }}</span>
                <span v-if="clockInRuleAlertVisible" class="rule-alert-dot">!</span>
                <span v-else class="green-dot" />
                <span>下班 {{ expectedOutText }}</span>
                <span v-if="clockOutRuleAlertVisible" class="rule-alert-dot">!</span>
                <span v-else class="green-dot" />
                <span class="chevron">›</span>
              </button>
            </div>
          </section>

          <button
            v-if="todayHasAbnormal"
            type="button"
            class="section-block abnormal-entry-card"
            @click="openTodayAbnormal"
          >
            <span class="abnormal-entry-card__left">
              <span class="abnormal-entry-card__icon">☰</span>
              <strong>本月待处理异常</strong>
            </span>
            <span class="abnormal-entry-card__meta">{{ monthAbnormalDaysText }}</span>
            <span class="chevron">›</span>
          </button>
        </template>

        <template v-else>
          <section class="section-block solid-card device-card">
            <h2>考勤机</h2>
            <p>当前移动端未绑定实体考勤机，打卡将按当前考勤规则校验定位与照片。</p>
          </section>
        </template>

        <nav v-if="showQuickNav" class="attendance-quick-nav">
          <button type="button" class="quick-nav-item" :class="{ 'is-active': activeMainTab === 'punch' || activeMainTab === 'clock_out_result' }" @click="activeMainTab = 'punch'">
            <span class="quick-nav-icon">⌖</span>
            <span>打卡</span>
          </button>
          <button type="button" class="quick-nav-item" :class="{ 'is-active': activeMainTab === 'apply' }" @click="openAttendanceApplyCenter">
            <span class="quick-nav-icon">＋</span>
            <span>申请</span>
          </button>
          <button type="button" class="quick-nav-item" :class="{ 'is-active': activeMainTab === 'stats' }" @click="activeMainTab = 'stats'">
            <span class="quick-nav-icon">◷</span>
            <span>统计</span>
          </button>
          <button type="button" class="quick-nav-item" :class="{ 'is-active': activeMainTab === 'device' }" @click="activeMainTab = 'device'">
            <span class="quick-nav-icon">▣</span>
            <span>考勤机</span>
          </button>
        </nav>
      </div>

      <div v-if="addressPickerVisible" class="mobile-picker-mask" @click.self="addressPickerVisible = false">
        <section class="address-picker-panel">
          <div class="address-picker-map">
            <div ref="addressPickerMapRef" class="address-picker-map__inner" />
            <button type="button" class="address-picker-confirm" @click="confirmCurrentAddress">确定</button>
          </div>
          <div class="address-picker-sheet">
            <div class="address-picker-handle" />
            <label class="address-search-box">
              <span>⌕</span>
              <input v-model="addressKeyword" placeholder="搜索" @input="searchOutsidePlaces" />
            </label>
            <p class="address-picker-tip">可选 300 米范围之内的地点</p>
            <button
              v-for="item in addressSuggestions"
              :key="`${item.title}-${item.latitude}-${item.longitude}`"
              type="button"
              class="address-suggestion-row"
              :class="{ 'is-selected': selectedOutsidePlace?.title === item.title }"
              @click="selectOutsidePlace(item)"
            >
              <strong>{{ item.title }}</strong>
              <span>{{ item.address }}</span>
              <em v-if="selectedOutsidePlace?.title === item.title">✓</em>
            </button>
          </div>
        </section>
      </div>

      <div v-if="photoPreviewVisible" class="mobile-picker-mask" @click.self="photoPreviewVisible = false">
        <section class="photo-preview-panel">
          <img :src="outsidePhotoPreview" alt="水印打卡图片" />
          <button type="button" @click="photoPreviewVisible = false">关闭</button>
        </section>
      </div>

      <div v-if="earlyLeaveDialog.open" class="mobile-picker-mask" @click.self="resolveEarlyLeaveDialog(false)">
        <section class="early-leave-panel" role="dialog" aria-modal="true" aria-labelledby="early-leave-title">
          <div class="early-leave-icon">⌁</div>
          <h2 id="early-leave-title">你早退了</h2>
          <strong>{{ earlyLeaveDialog.time }}</strong>
          <p v-if="earlyLeaveDialog.message">{{ earlyLeaveDialog.message }}</p>
          <footer>
            <button type="button" @click="resolveEarlyLeaveDialog(false)">取消</button>
            <button type="button" @click="resolveEarlyLeaveDialog(true)">确认打卡</button>
          </footer>
        </section>
      </div>

      <div v-if="outOfRangeDialog.open" class="mobile-picker-mask" @click.self="resolveOutOfRangeDialog(false)">
        <section class="range-confirm-panel" role="dialog" aria-modal="true" aria-labelledby="range-confirm-title">
          <div class="range-confirm-pin" aria-hidden="true">
            <span>?</span>
          </div>
          <h2 id="range-confirm-title">不在打卡范围内</h2>
          <p>{{ outOfRangeDialog.reason }}</p>
          <div class="range-confirm-proof">
            <span class="range-confirm-check">✓</span>
            <span>拍照上传水印图片作为辅助证明</span>
          </div>
          <footer>
            <button type="button" @click="resolveOutOfRangeDialog(false)">取消</button>
            <button type="button" @click="resolveOutOfRangeDialog(true)">确认打卡</button>
          </footer>
        </section>
      </div>

      <div v-if="statusFilterVisible" class="mobile-picker-mask" @click.self="statusFilterVisible = false">
        <section class="status-filter-panel" role="dialog" aria-modal="true" aria-labelledby="status-filter-title">
          <h2 id="status-filter-title">校准状态</h2>
          <button
            v-for="option in statsStatusFilterOptions"
            :key="option.value"
            type="button"
            :class="{ 'is-selected': statsStatusFilter === option.value }"
            @click="selectStatsStatusFilter(option.value)"
          >
            <span>{{ option.label }}</span>
            <em v-if="statsStatusFilter === option.value">✓</em>
          </button>
          <footer>
            <button type="button" @click="statusFilterVisible = false">取消</button>
          </footer>
        </section>
      </div>

      <div v-if="leaveFilterVisible" class="mobile-picker-mask" @click.self="leaveFilterVisible = false">
        <section class="status-filter-panel" role="dialog" aria-modal="true">
          <h2>假勤类型</h2>
          <button
            v-for="option in leaveFilterOptions"
            :key="option.value"
            type="button"
            :class="{ 'is-selected': leaveTypeFilter === option.value }"
            @click="selectLeaveTypeFilter(option.value)"
          >
            <span>{{ option.label }}</span>
            <em v-if="leaveTypeFilter === option.value">✓</em>
          </button>
          <footer>
            <button type="button" @click="leaveFilterVisible = false">取消</button>
          </footer>
        </section>
      </div>

      <div v-if="overtimeDateTypeFilterVisible" class="mobile-picker-mask" @click.self="overtimeDateTypeFilterVisible = false">
        <section class="status-filter-panel" role="dialog" aria-modal="true">
          <h2>日期类型</h2>
          <button
            v-for="option in overtimeDateTypeOptions"
            :key="option.value"
            type="button"
            :class="{ 'is-selected': overtimeDateTypeFilter === option.value }"
            @click="selectOvertimeDateTypeFilter(option.value)"
          >
            <span>{{ option.label }}</span>
            <em v-if="overtimeDateTypeFilter === option.value">✓</em>
          </button>
          <footer>
            <button type="button" @click="overtimeDateTypeFilterVisible = false">取消</button>
          </footer>
        </section>
      </div>

      <div v-if="overtimeSettlementFilterVisible" class="mobile-picker-mask" @click.self="overtimeSettlementFilterVisible = false">
        <section class="status-filter-panel" role="dialog" aria-modal="true">
          <h2>核算方式</h2>
          <button
            v-for="option in overtimeSettlementOptions"
            :key="option.value"
            type="button"
            :class="{ 'is-selected': overtimeSettlementFilter === option.value }"
            @click="selectOvertimeSettlementFilter(option.value)"
          >
            <span>{{ option.label }}</span>
            <em v-if="overtimeSettlementFilter === option.value">✓</em>
          </button>
          <footer>
            <button type="button" @click="overtimeSettlementFilterVisible = false">取消</button>
          </footer>
        </section>
      </div>

      <div v-if="exportStatusPickerVisible" class="mobile-picker-mask" @click.self="exportStatusPickerVisible = false">
        <section class="status-filter-panel" role="dialog" aria-modal="true">
          <h2>选择打卡状态</h2>
          <button
            v-for="option in exportStatusOptions"
            :key="option.value"
            type="button"
            :class="{ 'is-selected': exportStatusFilter === option.value }"
            @click="selectExportStatusFilter(option.value)"
          >
            <span>{{ option.label }}</span>
            <em v-if="exportStatusFilter === option.value">✓</em>
          </button>
          <footer>
            <button type="button" @click="exportStatusPickerVisible = false">取消</button>
          </footer>
        </section>
      </div>

      <div v-if="exportRangePickerVisible" class="mobile-picker-mask" @click.self="exportRangePickerVisible = false">
        <section class="status-filter-panel" role="dialog" aria-modal="true">
          <h2>选择时间范围</h2>
          <button
            v-for="option in exportRangeOptions"
            :key="option.value"
            type="button"
            :class="{ 'is-selected': statsRange === option.value }"
            @click="selectExportRange(option.value)"
          >
            <span>{{ option.label }}</span>
            <em v-if="statsRange === option.value">✓</em>
          </button>
          <footer>
            <button type="button" @click="exportRangePickerVisible = false">取消</button>
          </footer>
        </section>
      </div>

      <div v-if="applicationSheetVisible" class="mobile-picker-mask" @click.self="applicationSheetVisible = false">
        <section class="application-sheet-panel" role="dialog" aria-modal="true">
          <header>
            <strong>提交申请（{{ applicationSheetDateText }}）</strong>
            <button type="button" @click="applicationSheetVisible = false">×</button>
          </header>
          <button type="button" @click="chooseApplicationAction('legal_overtime')">
            <span class="sheet-action-icon is-cyan">♟</span>
            <span>法定节假日加班申请</span>
            <em>›</em>
          </button>
          <button type="button" @click="chooseApplicationAction('leave')">
            <span class="sheet-action-icon is-yellow">▣</span>
            <span>请假</span>
            <em>›</em>
          </button>
          <button type="button" @click="chooseApplicationAction('punch_correction')">
            <span class="sheet-action-icon is-blue">⌖</span>
            <span>打卡补卡</span>
            <em>›</em>
          </button>
          <button type="button" @click="chooseApplicationAction('business_trip')">
            <span class="sheet-action-icon is-sky">⌄</span>
            <span>出差</span>
            <em>›</em>
          </button>
          <button type="button" @click="chooseApplicationAction('outside')">
            <span class="sheet-action-icon is-blue">▤</span>
            <span>外出</span>
            <em>›</em>
          </button>
        </section>
      </div>

      <ion-toast
        :is-open="toast.open"
        :message="toast.message"
        :color="toast.color"
        :duration="1800"
        position="top"
        @didDismiss="toast.open = false"
      />
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  IonContent,
  IonIcon,
  IonInput,
  IonPage,
  IonToast,
  onIonViewDidLeave,
  onIonViewWillEnter,
} from '@ionic/vue'
import {
  airplaneOutline,
  bagHandleOutline,
  calendarOutline,
  locationOutline,
  personAddOutline,
  shieldCheckmarkOutline,
  timeOutline,
} from 'ionicons/icons'
import { useAuthStore } from '@/stores/auth'
import { get, post } from '@/utils/request'
import {
  clearMobilePositionWatch,
  getCurrentMobilePosition,
  getMobileGeolocationErrorMessage,
  isMobileGeolocationSupported,
  type MobileGeoPosition,
  type MobileGeoWatchId,
  watchMobilePosition,
} from '@/utils/geolocation'
import {
  CHINA_TIME_ZONE,
  chinaDateString,
  formatDateTime,
  formatStatus,
  monthRangeFromYmd,
  shiftYmdDays,
  shiftYmdMonths,
  weekRangeFromYmd,
} from '@/utils/format'

declare global {
  interface Window {
    TMap?: any
    __tencentMapSdkLoading?: Promise<void>
  }
}

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

type MainTab = 'punch' | 'apply' | 'stats' | 'device' | 'rule_detail' | 'month_detail' | 'export_report' | 'punch_detail' | 'clock_out_result'
type StatsStatusFilter = 'all' | 'normal' | 'rest' | 'not_scheduled' | 'abnormal' | 'late' | 'early_leave' | 'missed_clock' | 'absent'
type MonthDetailTab = 'attendance' | 'leave' | 'overtime'
type LeaveTypeFilter = 'all' | 'leave' | 'punch_correction' | 'outside' | 'business_trip' | 'field_work'
type OvertimeDateTypeFilter = 'all' | 'workday' | 'restday' | 'holiday'
type OvertimeSettlementFilter = 'all' | 'comp_time' | 'overtime_pay' | 'none'
type ExportStatusFilter = 'all' | 'normal' | 'abnormal'
type OvertimeMonthRow = {
  id: string
  year: number
  month: number
  monthLabel: string
  hoursText: string
  hours: number
  sourceText: string
}
type AttendanceApprovalTemplateTile = {
  key: string
  name: string
  category: string
  businessCode: string
  codeAliases?: string[]
  icon: string
  color: 'yellow' | 'teal' | 'blue'
  source?: any
}
type AttendanceApprovalTemplateGridSlot = {
  key: string
  placeholder?: false
  template: AttendanceApprovalTemplateTile
} | {
  key: string
  placeholder: true
  template?: never
}

const activeMainTab = ref<MainTab>('punch')
const punchScene = ref<'normal' | 'outside'>('normal')
const statsRange = ref<'day' | 'week' | 'month'>('day')
const monthDetailTab = ref<MonthDetailTab>('attendance')
const statsRangeOptions = [
  { label: '日', value: 'day' as const },
  { label: '周', value: 'week' as const },
  { label: '月', value: 'month' as const },
]
const monthDetailTabs: Array<{ value: MonthDetailTab; label: string }> = [
  { value: 'attendance', label: '上下班打卡' },
  { value: 'leave', label: '假勤' },
  { value: 'overtime', label: '加班' },
]

const outsideClient = ref('')
const outsideRemark = ref('')
const outsidePhotoReady = ref(false)
const outsidePhotoUrl = ref('')
const outsidePhotoPreview = ref('')
const outsidePhotoStatusText = computed(() => {
  if (outsidePhotoUploading.value) return '上传中...'
  if (outsidePhotoReady.value) return '已拍摄打卡照片'
  return '拍摄水印照片'
})
const outsidePhotoUploading = ref(false)
const photoPreviewVisible = ref(false)
const punchSubmitting = ref(false)
const statsExporting = ref(false)
const monthlyConfirmSubmitting = ref(false)
const statusFilterVisible = ref(false)
const leaveFilterVisible = ref(false)
const overtimeDateTypeFilterVisible = ref(false)
const overtimeSettlementFilterVisible = ref(false)
const exportStatusPickerVisible = ref(false)
const exportRangePickerVisible = ref(false)
const statsStatusFilter = ref<StatsStatusFilter>('all')
const leaveTypeFilter = ref<LeaveTypeFilter>('all')
const overtimeDateTypeFilter = ref<OvertimeDateTypeFilter>('all')
const overtimeSettlementFilter = ref<OvertimeSettlementFilter>('all')
const exportStatusFilter = ref<ExportStatusFilter>('all')
const selectedPunchDetailRecord = ref<any | null>(null)
const selectedPunchDetailType = ref<'check_in' | 'check_out'>('check_in')
const applicationSheetVisible = ref(false)
const applicationSheetRecord = ref<any | null>(null)
const earlyLeaveDialog = reactive({
  open: false,
  time: '',
  message: '',
})
const outOfRangeDialog = reactive({
  open: false,
  reason: '地理位置错误',
})
const openedRuleSections = reactive({
  time: true,
  range: true,
  overtime: true,
  correction: true,
  more: true,
})
const mapLoading = ref(false)
const mapPreview = ref('')
const mapReady = ref(false)
const mapLoadError = ref('')
const mapExpanded = ref(false)
const mapContainerRef = ref<HTMLDivElement | null>(null)
const addressPickerMapRef = ref<HTMLDivElement | null>(null)
const outsidePhotoInputRef = ref<HTMLInputElement | null>(null)
const currentPlaceName = ref('')
const reverseAddress = ref('')
const runtime = ref<any | null>(null)
const geofenceDistanceMeters = ref<number | null>(null)
const isInsideGeofence = ref<boolean | null>(null)
const tencentMapConfig = ref<{ key: string; script_url: string; version: string } | null>(null)
let tencentMap: any = null
let addressPickerMap: any = null
let punchMapMarkers: any = null
let punchMapCircle: any = null
let mapCenterTimer: number | null = null
let geoWatchId: MobileGeoWatchId | null = null
let geoWatchStartToken = 0
let earlyLeaveDialogResolver: ((confirmed: boolean) => void) | null = null
let outOfRangeDialogResolver: ((confirmed: boolean) => void) | null = null
const tencentCoordCache = new Map<string, { lat: number; lng: number }>()
const tencentAddressCache = new Map<string, { placeName: string; address: string }>()
let tencentSyncPromise: Promise<boolean> | null = null
let tencentSyncKey = ''

type PlaceSuggestion = {
  title: string
  address: string
  latitude: number
  longitude: number
}

const addressPickerVisible = ref(false)
const addressKeyword = ref('')
const addressSuggestions = ref<PlaceSuggestion[]>([])
const selectedOutsidePlace = ref<PlaceSuggestion | null>(null)

const toast = reactive({
  open: false,
  message: '',
  color: 'primary' as 'primary' | 'success' | 'warning' | 'danger',
})

const geo = reactive({
  lat: null as number | null,
  lng: null as number | null,
  rawLat: null as number | null,
  rawLng: null as number | null,
  accuracy: null as number | null,
  error: '',
  transformError: '',
  tencentSyncing: false,
  source: '' as 'gps' | 'tencent_ip' | '',
  coordinateSystem: '' as 'tencent' | 'wgs84' | '',
})

const todayRecord = ref<any | null>(null)
const statsRecords = ref<any[]>([])
const monthSummary = ref<any | null>(null)
const mobileDashboard = ref<any | null>(null)

const cstClockText = ref('--:--')
const cstNowText = ref('--')
let clockTimer: number | null = null
let runtimeRefreshTimer: number | null = null

const statsDate = ref('')
const attendanceTemplateLoading = ref(false)
const attendanceTemplatesLoaded = ref(false)
const attendanceTemplateSearch = ref('')
const attendanceTemplateGroups = ref<any[]>([])

const attendanceTemplateCatalog: Array<Omit<AttendanceApprovalTemplateTile, 'key' | 'source'>> = [
  { category: '人事', name: '法定节假日加班申请', businessCode: 'legal_overtime', codeAliases: ['overtime_holiday', 'holiday_overtime'], icon: timeOutline, color: 'blue' },
  { category: '人事', name: '请假', businessCode: 'leave', codeAliases: ['leave_request'], icon: calendarOutline, color: 'yellow' },
  { category: '人事', name: '出差', businessCode: 'business_trip', codeAliases: ['travel'], icon: airplaneOutline, color: 'blue' },
  { category: '人事', name: '外出', businessCode: 'outside', codeAliases: ['out', 'fieldwork'], icon: bagHandleOutline, color: 'blue' },
  { category: '人事', name: '离职', businessCode: 'resignation', icon: shieldCheckmarkOutline, color: 'teal' },
  { category: '人事', name: '招聘需求', businessCode: 'recruitment', codeAliases: ['recruitment_demand'], icon: personAddOutline, color: 'teal' },
  { category: '人事', name: '打卡补卡', businessCode: 'punch_correction', codeAliases: ['attendance_punch_correction'], icon: locationOutline, color: 'blue' },
]
const attendanceApprovalReturnQuery = {
  return_to: '/app/attendance?tab=apply',
}

const statsStatusFilterOptions: Array<{ value: StatsStatusFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'normal', label: '正常' },
  { value: 'rest', label: '休息/未排班' },
  { value: 'abnormal', label: '全部异常' },
  { value: 'late', label: '迟到' },
  { value: 'early_leave', label: '早退' },
  { value: 'missed_clock', label: '缺卡' },
  { value: 'absent', label: '旷工' },
]

const leaveFilterOptions: Array<{ value: LeaveTypeFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'leave', label: '请假' },
  { value: 'punch_correction', label: '补卡' },
  { value: 'outside', label: '外出' },
  { value: 'business_trip', label: '出差' },
  { value: 'field_work', label: '外勤' },
]

const overtimeDateTypeOptions: Array<{ value: OvertimeDateTypeFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'workday', label: '工作日加班' },
  { value: 'restday', label: '休息日加班' },
  { value: 'holiday', label: '节假日加班' },
]

const overtimeSettlementOptions: Array<{ value: OvertimeSettlementFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'comp_time', label: '调休' },
  { value: 'overtime_pay', label: '加班费' },
  { value: 'none', label: '无核算方式' },
]

const exportStatusOptions: Array<{ value: ExportStatusFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'normal', label: '正常' },
  { value: 'abnormal', label: '异常' },
]

const exportRangeOptions: Array<{ value: 'day' | 'week' | 'month'; label: string }> = [
  { value: 'day', label: '今日' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
]

const visibleAttendanceTemplateGroups = computed(() => {
  const keyword = attendanceTemplateSearch.value.trim().toLowerCase()
  return mergedAttendanceTemplateGroups()
    .map((group) => ({
      ...group,
      templates: keyword
        ? group.templates.filter((template) => template.name.toLowerCase().includes(keyword))
        : group.templates,
    }))
    .filter((group) => group.templates.length > 0)
})

function syncAttendanceTabFromRoute() {
  const tab = String(Array.isArray(route.query.tab) ? route.query.tab[0] : route.query.tab || '')
  if (tab === 'apply') {
    activeMainTab.value = 'apply'
    void loadAttendanceApprovalTemplates()
  }
}

function normalizeTemplateKey(value: unknown) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '_')
}

function attendanceTemplateBusinessCode(template: any) {
  return normalizeTemplateKey(template?.business_code || template?.code || template?.key || template?.name)
}

function attendanceTemplateCategory(group: any) {
  return String(group?.category || group?.name || '通用审批').trim()
}

function attendanceGroupTemplates(group: any) {
  return group?.types || group?.templates || []
}

function isAttendanceRuleBindingTemplate(template: any) {
  return attendanceTemplateBusinessCode(template).startsWith('attendance_rule_')
}

function matchedAttendanceCatalogItem(template: any, category: string) {
  const code = attendanceTemplateBusinessCode(template)
  const name = String(template?.name || '').trim()
  return attendanceTemplateCatalog.find((item) => {
    const codes = [item.businessCode, ...(item.codeAliases || [])].map(normalizeTemplateKey)
    return codes.includes(code) || (item.category === category && item.name === name)
  })
}

function fallbackAttendanceTemplateColor(category: string): AttendanceApprovalTemplateTile['color'] {
  if (category === '假勤管理' || category === '人事') return 'blue'
  if (category === '人事管理' || category === '行政' || category === '行政管理') return 'teal'
  return 'yellow'
}

function fallbackAttendanceTemplateIcon(category: string) {
  if (category === '人事管理' || category === '行政' || category === '行政管理') return personAddOutline
  return calendarOutline
}

function isHiddenAttendanceApprovalTemplate(template: any) {
  const code = attendanceTemplateBusinessCode(template)
  const name = String(template?.name || '').trim()
  return ['headcount_request', 'headcount_plan'].includes(code) || name === '用人审批'
}

function attendanceTemplateGridSlots(templates: AttendanceApprovalTemplateTile[]): AttendanceApprovalTemplateGridSlot[] {
  const slots: AttendanceApprovalTemplateGridSlot[] = templates.map((template) => ({
    key: template.key,
    template,
  }))
  const remainder = templates.length % 3
  if (!remainder) return slots
  for (let index = 0; index < 3 - remainder; index += 1) {
    slots.push({
      key: `placeholder-${templates[0]?.category || 'group'}-${templates.length}-${index}`,
      placeholder: true,
    })
  }
  return slots
}

function mergedAttendanceTemplateGroups() {
  const groups = new Map<string, AttendanceApprovalTemplateTile[]>()
  const ensureGroup = (category: string) => {
    if (!groups.has(category)) groups.set(category, [])
    return groups.get(category)!
  }
  const addTile = (tile: AttendanceApprovalTemplateTile) => {
    if (tile.source) {
      for (const list of groups.values()) {
        const fallbackIndex = list.findIndex((item) => !item.source && (
          item.businessCode === tile.businessCode || item.name === tile.name
        ))
        if (fallbackIndex >= 0) list.splice(fallbackIndex, 1)
      }
    }
    const list = ensureGroup(tile.category)
    const existingIndex = list.findIndex((item) => item.businessCode === tile.businessCode || item.name === tile.name)
    if (existingIndex >= 0) {
      list[existingIndex] = { ...list[existingIndex], ...tile }
    } else {
      list.push(tile)
    }
  }

  for (const item of attendanceTemplateCatalog) {
    addTile({ ...item, key: `catalog-${item.businessCode}` })
  }

  for (const group of attendanceTemplateGroups.value) {
    const category = attendanceTemplateCategory(group)
    for (const template of attendanceGroupTemplates(group)) {
      if (isAttendanceRuleBindingTemplate(template)) continue
      if (isHiddenAttendanceApprovalTemplate(template)) continue
      const catalogItem = matchedAttendanceCatalogItem(template, category)
      const name = String(template?.name || catalogItem?.name || '未命名申请').trim()
      const businessCode = attendanceTemplateBusinessCode(template)
      addTile({
        key: `template-${template?.id || businessCode || name}`,
        name: catalogItem?.name || name,
        category: catalogItem?.category || category || '通用审批',
        businessCode: catalogItem?.businessCode || businessCode || normalizeTemplateKey(name),
        icon: catalogItem?.icon || fallbackAttendanceTemplateIcon(category),
        color: catalogItem?.color || fallbackAttendanceTemplateColor(category),
        source: template,
      })
    }
  }

  const orderedCategories = ['人事', '财务', '行政', '智能财务', '行政管理', '假勤管理', '人事管理', '法务管理', '业务管理', '其他']
  const categories = Array.from(groups.keys()).sort((a, b) => {
    const aIndex = orderedCategories.indexOf(a)
    const bIndex = orderedCategories.indexOf(b)
    if (aIndex >= 0 || bIndex >= 0) return (aIndex >= 0 ? aIndex : 99) - (bIndex >= 0 ? bIndex : 99)
    return a.localeCompare(b, 'zh-Hans-CN')
  })
  return categories
    .map((category) => ({ category, templates: groups.get(category)! }))
    .filter((group) => group.templates.length)
}

async function loadAttendanceApprovalTemplates(force = false) {
  if (attendanceTemplateLoading.value) return
  if (attendanceTemplatesLoaded.value && !force) return
  attendanceTemplateLoading.value = true
  try {
    attendanceTemplateGroups.value = await get<any[]>('/approval/templates')
    attendanceTemplatesLoaded.value = true
  } catch {
    attendanceTemplateGroups.value = []
    attendanceTemplatesLoaded.value = true
    showToast('审批模板加载失败', 'danger')
  } finally {
    attendanceTemplateLoading.value = false
  }
}

function goBack() {
  if (activeMainTab.value === 'export_report') {
    activeMainTab.value = 'stats'
    return
  }
  if (activeMainTab.value === 'clock_out_result') {
    activeMainTab.value = 'punch'
    return
  }
  if (activeMainTab.value === 'punch_detail') {
    activeMainTab.value = statsRange.value === 'month' ? 'month_detail' : 'stats'
    return
  }
  if (activeMainTab.value === 'month_detail') {
    activeMainTab.value = 'stats'
    return
  }
  if (activeMainTab.value === 'rule_detail') {
    activeMainTab.value = 'punch'
    return
  }
  if (activeMainTab.value === 'stats' || activeMainTab.value === 'apply' || activeMainTab.value === 'device') {
    activeMainTab.value = 'punch'
    return
  }
  if (window.history.length > 1) {
    router.back()
    return
  }
  void router.push('/app/tabs/workbench')
}

function setGlobalTabbarVisible(visible: boolean) {
  document.querySelectorAll('ion-tab-bar.mobile-tabbar').forEach((bar) => {
    ;(bar as HTMLElement).style.display = visible ? '' : 'none'
  })
}

function showToast(message: string, color: 'primary' | 'success' | 'warning' | 'danger' = 'primary') {
  toast.message = message
  toast.color = color
  toast.open = true
}

function cstDateString(date: Date) {
  return chinaDateString(date) || ''
}

function updateClockText() {
  const now = new Date()
  cstClockText.value = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: CHINA_TIME_ZONE,
  }).format(now)
  cstNowText.value = new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
    timeZone: CHINA_TIME_ZONE,
  }).format(now)
}

function haversineMeters(lat1: number, lng1: number, lat2: number, lng2: number) {
  const toRad = (deg: number) => (deg * Math.PI) / 180
  const earthRadius = 6371000
  const dLat = toRad(lat2 - lat1)
  const dLng = toRad(lng2 - lng1)
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2)
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2))
    * Math.sin(dLng / 2) * Math.sin(dLng / 2)
  return 2 * earthRadius * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

const todayCst = computed(() => cstDateString(new Date()))

const attendanceTitle = computed(() => {
  const titleMap: Record<MainTab, string> = {
    punch: '打卡',
    apply: '发起申请',
    stats: '统计',
    device: '考勤机',
    rule_detail: '打卡规则详情',
    month_detail: statsRange.value === 'week' ? '我的周明细' : '我的月明细',
    export_report: '导出',
    punch_detail: '打卡详情',
    clock_out_result: '打卡',
  }
  return titleMap[activeMainTab.value]
})

const attendanceSubtitle = computed(() => {
  if (activeMainTab.value !== 'month_detail') return ''
  const start = String(mobileDashboard.value?.range?.start_date || currentStatsRange().start)
  const end = String(mobileDashboard.value?.range?.end_date || currentStatsRange().end)
  if (!start || !end) return ''
  if (statsRange.value === 'week') {
    return `${Number(start.slice(5, 7))}月${Number(start.slice(8, 10))}日-${Number(end.slice(5, 7))}月${Number(end.slice(8, 10))}日`
  }
  return `${Number(start.slice(0, 4))}年${Number(start.slice(5, 7))}月${Number(start.slice(8, 10))}日 - ${Number(end.slice(5, 7))}月${Number(end.slice(8, 10))}日`
})

const headerRightIcon = computed(() => (
  activeMainTab.value === 'month_detail' ? '◷' : '···'
))

function handleHeaderAction() {
  if (activeMainTab.value === 'month_detail') {
    activeMainTab.value = 'export_report'
    return
  }
  activeMainTab.value = 'device'
}

function openOutsidePunchFromResult() {
  activeMainTab.value = 'punch'
  punchScene.value = 'outside'
}

const showQuickNav = computed(() => ['punch', 'clock_out_result', 'apply', 'stats', 'device'].includes(activeMainTab.value))

const punchPhase = computed<'clock_in' | 'clock_out' | 'done'>(() => {
  const runtimePhase = runtime.value?.punch_preview?.phase
  if (runtimePhase === 'clock_in' || runtimePhase === 'clock_out') {
    return runtimePhase
  }
  if (!todayRecord.value?.clock_in_time) return 'clock_in'
  return 'clock_out'
})

const isRuntimeReady = computed(() => Boolean(runtime.value?.has_rule && runtime.value?.punch_preview))
const punchDisabled = computed(() => !isRuntimeReady.value)
const punchButtonText = computed(() => (punchPhase.value === 'clock_in' ? '上班打卡' : punchPhase.value === 'clock_out' ? '下班打卡' : '已完成'))
const mainPunchButtonText = computed(() => {
  if (punchPhase.value === 'clock_out' && todayRecord.value?.clock_out_time) return '更新下班卡'
  return punchButtonText.value
})
const expectedInText = computed(() => runtime.value?.expected?.clock_in || '--:--')
const expectedOutText = computed(() => runtime.value?.expected?.clock_out || '--:--')
function normalizePunchStatus(value?: string | null) {
  const text = String(value || '')
  if (text === '迟到') return 'late'
  if (text === '早退') return 'early_leave'
  if (text === '旷工') return 'absent'
  if (text === '未到下班时间') return 'before_off_duty'
  if (text === '已完成') return 'done'
  if (text === '正常') return 'normal'
  return text || 'normal'
}

const punchRuleStatus = computed(() => {
  if (punchPhase.value === 'done') {
    return { status: 'done', minutes: 0 }
  }
  if (!runtime.value) {
    return { status: 'rule_unavailable', minutes: 0 }
  }
  if (runtime.value.has_rule === false) {
    return { status: 'rule_unavailable', minutes: 0 }
  }
  const preview = runtime.value?.punch_preview || {}
  if (!preview.status && !preview.status_text) {
    return { status: 'checking', minutes: 0 }
  }
  const rawStatus = normalizePunchStatus(preview.status || preview.status_text)
  return {
    status: rawStatus === 'done' && punchPhase.value === 'clock_out' ? 'normal' : rawStatus,
    minutes: Number(preview.minutes || 0),
  }
})

const punchUiState = computed(() => {
  if (normalPunchBlocked.value) {
    return { tone: 'danger', message: '当前不在打卡范围内，可异地打卡并记录异常' }
  }
  if (punchRuleStatus.value.status === 'checking') {
    return { tone: 'pending', message: '正在校验考勤规则，请稍候' }
  }
  if (punchRuleStatus.value.status === 'rule_unavailable') {
    return { tone: 'danger', message: '未获取到当前员工考勤规则，请刷新或联系管理员' }
  }
  if (punchRuleStatus.value.status === 'late') {
    return { tone: 'late', message: '已超过上班时间，本次将记为迟到' }
  }
  if (punchRuleStatus.value.status === 'absent') {
    return { tone: 'danger', message: '已超过半天，本次将按旷工半天处理' }
  }
  if (punchRuleStatus.value.status === 'early_leave') {
    return { tone: 'early', message: '未到下班时间，本次将记为早退' }
  }
  if (punchRuleStatus.value.status === 'before_off_duty') {
    return { tone: 'pending', message: '未到下班时间' }
  }
  if (punchRuleStatus.value.status === 'done') {
    return { tone: 'done', message: '今日上下班打卡已完成' }
  }
  return { tone: 'normal', message: '当前可正常打卡，系统会记录本次位置与时间' }
})

const punchCircleTitle = computed(() => {
  if (punchRuleStatus.value.status === 'checking') return '校验中'
  if (punchRuleStatus.value.status === 'rule_unavailable') return '规则异常'
  return mainPunchButtonText.value
})

const punchCircleHint = computed(() => {
  if (punchRuleStatus.value.status === 'checking') return '请稍候'
  if (punchRuleStatus.value.status === 'rule_unavailable') return '请刷新'
  if (punchRuleStatus.value.status === 'before_off_duty') return `请 ${expectedOutText.value} 后打卡`
  if (punchRuleStatus.value.status === 'late') return `请 ${expectedInText.value} 前打卡`
  if (punchRuleStatus.value.status === 'absent') return `从 ${runtime.value?.check?.half_day_pm_start || '下午上班'} 起算`
  if (punchRuleStatus.value.status === 'early_leave') return `请 ${expectedOutText.value} 后打卡`
  return ''
})

function hmToMinutes(value?: string | null) {
  const match = String(value || '').match(/^(\d{1,2}):(\d{2})$/)
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

function clockValueToCstMinutes(value?: string | null) {
  if (!value) return null
  // 与 formatDateTime 保持一致：统一按中国时区展示值解析，避免显示与判定不一致。
  const formatted = formatDateTime(String(value))
  const match = formatted.match(/(\d{2}):(\d{2})/)
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

function isClockOutBeforeExpected(record?: any | null) {
  const clockOutStatus = formatStatus(record?.clock_out_status || '')
  if (clockOutStatus.includes('早退')) return true
  return recordDisplayStatus(record).includes('早退')
}

function resolveEarlyLeaveDialog(confirmed: boolean) {
  earlyLeaveDialog.open = false
  const resolver = earlyLeaveDialogResolver
  earlyLeaveDialogResolver = null
  resolver?.(confirmed)
}

function confirmEarlyLeavePunch() {
  earlyLeaveDialog.time = cstClockText.value
  earlyLeaveDialog.message = ''
  earlyLeaveDialog.open = true
  return new Promise<boolean>((resolve) => {
    earlyLeaveDialogResolver = resolve
  })
}

function resolveOutOfRangeDialog(confirmed: boolean) {
  outOfRangeDialog.open = false
  const resolver = outOfRangeDialogResolver
  outOfRangeDialogResolver = null
  resolver?.(confirmed)
}

function confirmOutOfRangePunch() {
  outOfRangeDialog.reason = '地理位置错误'
  outOfRangeDialog.open = true
  return new Promise<boolean>((resolve) => {
    outOfRangeDialogResolver = resolve
  })
}

function isEarlyLeavePunch(validation: any) {
  const status = normalizePunchStatus(validation?.status || validation?.status_text || punchRuleStatus.value.status)
  return status === 'early_leave' || status === 'before_off_duty'
}

async function confirmAbnormalPunch(validation: any) {
  const anomalies = Array.isArray(validation?.anomalies) ? validation.anomalies.map((item: any) => String(item)) : []
  const hasLocationIssue = normalPunchBlocked.value || anomalies.some((item: string) => item.includes('打卡范围') || item.includes('GPS定位'))
  const hasWindowIssue = anomalies.some((item: string) => item.includes('可打卡时段'))
  const detail = anomalies.length ? `\n\n异常原因：${anomalies.join('；')}` : ''
  if (hasLocationIssue) {
    return confirmOutOfRangePunch()
  }
  if (hasWindowIssue) {
    return window.confirm(`当前不在规定打卡时间内，确定继续打卡吗？系统会记录本次打卡时间并参与后续考勤、加班、补贴和调休计算。${detail}`)
  }
  return window.confirm(`当前打卡存在异常，确定继续打卡吗？系统会记录异常原因。${detail}`)
}
const normalPunchBlocked = computed(() => {
  const needGeoFence = !!runtime.value?.rule?.require_gps
  const mode = String(runtime.value?.rule?.check_method_mode || 'any').toLowerCase()
  const requireAllMethods = ['all', 'both', 'and'].includes(mode)
  const hasWifiAlternative = !!runtime.value?.rule?.require_wifi && !requireAllMethods
  return punchScene.value === 'normal' && needGeoFence && !hasWifiAlternative && isInsideGeofence.value === false
})

const geofenceMainText = computed(() => {
  if (punchScene.value === 'outside') return outsidePlaceTitle.value
  if (!runtime.value) return '正在读取考勤规则'
  if (runtime.value.has_rule === false) return '未获取到考勤规则'
  if (!runtime.value?.rule?.require_gps) return '此规则不要求 GPS 定位'
  if (!runtime.value?.map_service_enabled) return '请联系管理员配置定位服务'
  if (geo.error) return '定位失败'
  if (geo.tencentSyncing || geo.coordinateSystem !== 'tencent') return '正在同步腾讯地图定位'
  if (isInsideGeofence.value === true) return '你已在打卡范围内'
  if (isInsideGeofence.value === false) return '你不在打卡范围内'
  return '正在校验打卡范围'
})

const geofenceSubText = computed(() => {
  if (punchScene.value === 'outside') return outsidePlaceAddress.value
  if (!runtime.value) return '系统会先读取管理端当前员工命中的考勤规则'
  if (runtime.value.has_rule === false) return '请先在管理端为当前员工配置并启用考勤规则'
  if (geo.source === 'tencent_ip') return '当前为腾讯 IP 定位兜底；手机真 GPS 需要 HTTPS 或原生容器'
  if (geo.error) return geo.error
  if (geo.tencentSyncing) return '已获取 GPS，正在同步腾讯地图坐标'
  if (geo.coordinateSystem !== 'tencent' && geo.transformError) return '腾讯地图同步暂不可用，打卡前会自动重试'
  if (currentPlaceName.value && geofenceDistanceMeters.value !== null && runtime.value?.rule?.gps_radius_meters) {
    return `${currentPlaceName.value} · 距范围中心约 ${Math.round(geofenceDistanceMeters.value)} 米（半径 ${runtime.value.rule.gps_radius_meters} 米）`
  }
  if (currentPlaceName.value) return `${currentPlaceName.value} · 已接入腾讯地图 GPS 定位`
  if (geo.coordinateSystem === 'tencent' && geo.source === 'gps') return '已接入腾讯地图 GPS 定位'
  const mode = String(runtime.value?.rule?.check_method_mode || 'any').toLowerCase()
  const requireAllMethods = ['all', 'both', 'and'].includes(mode)
  if (runtime.value?.rule?.require_gps && runtime.value?.rule?.require_wifi && !requireAllMethods) {
    return '当前规则支持位置或 Wi-Fi 任一满足，最终以服务端打卡校验为准'
  }
  if (!runtime.value?.rule?.require_gps) return '当前规则未强制定位范围，系统将记录当前位置'
  if (isInsideGeofence.value === true) return runtime.value?.rule?.name || reverseAddress.value || '点击打卡后将自动同步至管理端'
  if (geofenceDistanceMeters.value !== null && runtime.value?.rule?.gps_radius_meters) {
    return `当前距范围中心约 ${Math.round(geofenceDistanceMeters.value)} 米（半径 ${runtime.value.rule.gps_radius_meters} 米）`
  }
  return '请确认当前位置或切换外出打卡'
})

const mapLoadingText = computed(() => {
  if (mapLoadError.value) return '腾讯地图加载失败，已切换定位预览'
  if (geo.error) return '等待浏览器定位授权'
  if (!runtime.value?.map_service_enabled) return runtime.value?.rule?.name || '打卡现场'
  return '腾讯地图定位中...'
})

const outsidePlaceTitle = computed(() => {
  if (selectedOutsidePlace.value?.title) return `${selectedOutsidePlace.value.title}›`
  const address = currentPlaceName.value || reverseAddress.value || locationTip.value || '正在获取外出位置'
  const normalized = address.replace(/^中国/, '').replace(/^北京市/, '')
  return normalized.length > 18 ? `${normalized.slice(0, 18)}…›` : `${normalized}›`
})

const outsidePlaceAddress = computed(() => {
  if (selectedOutsidePlace.value?.address) return selectedOutsidePlace.value.address
  if (currentPlaceName.value && reverseAddress.value) return `${currentPlaceName.value} · ${reverseAddress.value}`
  if (reverseAddress.value) return reverseAddress.value
  if (geo.lat !== null && geo.lng !== null) return `${geo.lat.toFixed(5)}, ${geo.lng.toFixed(5)}`
  return '定位中...'
})

const todayStatusText = computed(() => {
  if (!todayRecord.value) return '未打卡'
  return formatStatus(todayRecord.value.status || '-')
})

const abnormalStatusLabels = new Set(['迟到', '早退', '缺卡', '旷工'])
const abnormalKeywordHints = ['超出打卡范围', '打卡范围', '不在可打卡时段', 'GPS', 'WiFi', '定位', '异常']

function isAbnormalAttendanceStatus(value: any) {
  return abnormalStatusLabels.has(formatStatus(value || ''))
}

function isAbnormalAnomalyText(value: any) {
  const text = String(value || '').trim()
  if (!text) return false
  if ([...abnormalStatusLabels].some((item) => text.includes(item))) return true
  return abnormalKeywordHints.some((item) => text.includes(item))
}

function recordDisplayStatus(record: any) {
  const anomaly = String(record?.anomaly_type || '').trim()
  const status = formatStatus(record?.display_status || record?.status || '')
  if (anomaly && status && status !== '-' && !anomaly.includes(status)) return `${status}，${anomaly}`
  return anomaly || status || '-'
}

function isRecordAbnormal(record: any) {
  return Boolean(
    record?.is_abnormal
    || ['late', 'early_leave', 'missed_clock', 'absent', 'abnormal'].includes(String(record?.status || '').toLowerCase())
    || ['迟到', '早退', '缺卡', '旷工', '异常'].some((key) => String(record?.display_status || '').includes(key))
    || isAbnormalAnomalyText(record?.anomaly_type)
    || isAbnormalAttendanceStatus(record?.display_status || record?.status),
  )
}

function formatMonthDay(value: any) {
  const text = String(value || '')
  const month = Number(text.slice(5, 7))
  const day = Number(text.slice(8, 10))
  if (month && day) return `${month}月${day}日`
  return text || '-'
}

const dashboardDaily = computed(() => mobileDashboard.value?.daily || {})
const todayAnomalyText = computed(() => {
  return String(todayRecord.value?.anomaly_type || dashboardDaily.value?.anomaly_type || '').trim()
})
const todayEffectiveAnomalyText = computed(() => {
  return todayAnomalyText.value.split(';').map((item) => item.trim()).filter(Boolean).join('；')
})
const todayHasMethodOrRangeAbnormal = computed(() => isAbnormalAnomalyText(todayEffectiveAnomalyText.value))
const todayHasAbnormal = computed(() => {
  return Boolean(
    todayHasMethodOrRangeAbnormal.value
    || dailyClockInIsAbnormal.value
    || dailyClockOutIsAbnormal.value,
  )
})
const todayStatusDisplayText = computed(() => {
  const status = formatStatus(dashboardDaily.value?.status || todayRecord.value?.display_status || todayRecord.value?.status || '')
  if (status && status !== '-') return status
  if (!todayHasAbnormal.value) return '正常'
  return '异常'
})

const monthAbnormalDaysText = computed(() => {
  const count = Number(mobileDashboard.value?.monthly_abnormal?.pending_days || 0)
  return `${count || (todayHasAbnormal.value ? 1 : 0)}天`
})

const todayClockInShortText = computed(() => formatClockOnly(todayRecord.value?.clock_in_time))
const todayClockOutShortText = computed(() => formatClockOnly(todayRecord.value?.clock_out_time))
const todayAbnormalText = computed(() => {
  const text = todayEffectiveAnomalyText.value
  return text || (todayHasAbnormal.value ? todayStatusDisplayText.value : '-')
})

const clockInRuleAlertVisible = computed(() => {
  const status = punchRuleStatus.value.status
  if (status === 'late') return true
  if (['missed_clock', 'absent'].includes(normalizePunchStatus(status))) return true
  return dailyClockInIsAbnormal.value
})

const clockOutRuleAlertVisible = computed(() => {
  const hasClockOut = Boolean(mobileDashboard.value?.daily?.clock_out_time || todayRecord.value?.clock_out_time)
  if (!hasClockOut) return false
  const status = punchRuleStatus.value.status
  if (status === 'early_leave') return true
  if (['missed_clock', 'absent'].includes(normalizePunchStatus(status))) return true
  return dailyClockOutIsAbnormal.value
})

const ruleLineMarkerClass = computed(() => {
  if (clockInRuleAlertVisible.value || clockOutRuleAlertVisible.value) return 'is-hidden'
  return `is-${punchUiState.value.tone}`
})

const selectedDayAbnormalText = computed(() => {
  const anomaly = todayEffectiveAnomalyText.value
  const abnormalStatus = todayStatusDisplayText.value
  if (!anomaly && !todayHasAbnormal.value) return ''
  if (anomaly && abnormalStatus && abnormalStatus !== '正常' && !anomaly.includes(abnormalStatus)) return `${abnormalStatus}，${anomaly}`
  return anomaly || (abnormalStatus !== '正常' ? abnormalStatus : '') || '考勤异常'
})

const selectedDayCorrectionType = computed<'check_in' | 'check_out'>(() => {
  const text = selectedDayAbnormalText.value
  if (text.includes('早退') || text.includes('下班')) return 'check_out'
  if (text.includes('迟到') || text.includes('上班')) return 'check_in'
  return punchPhase.value === 'clock_out' ? 'check_out' : 'check_in'
})

const selectedStatsRecord = computed(() => {
  const target = statsDate.value || todayCst.value
  return attendanceDetailRows.value.find((record) => String(record?.date || '') === target)
    || (String(todayRecord.value?.date || '') === target ? todayRecord.value : null)
    || null
})

const activePunchDetailRecord = computed(() => selectedPunchDetailRecord.value || selectedStatsRecord.value || todayRecord.value || null)

const punchDetailDate = computed(() => {
  return String(activePunchDetailRecord.value?.date || mobileDashboard.value?.range?.target_date || statsDate.value || todayCst.value)
})

const punchDetailType = computed<'check_in' | 'check_out'>(() => {
  return selectedPunchDetailType.value
})

const punchDetailClockValue = computed(() => {
  const record = activePunchDetailRecord.value || {}
  if (runtime.value?.field_managed && activeMainTab.value === 'punch') return punchDetailType.value === 'check_out' ? record.clock_out_time : record.clock_in_time
  if (punchDetailType.value === 'check_out') return record.clock_out_time || mobileDashboard.value?.daily?.clock_out_time
  return record.clock_in_time || mobileDashboard.value?.daily?.clock_in_time
})

const punchDetailTimeText = computed(() => {
  const formatted = formatDateTime(punchDetailClockValue.value)
  return formatted === '-' ? `${punchDetailDate.value} ${punchDetailType.value === 'check_out' ? expectedOutText.value : expectedInText.value}` : formatted
})

const punchDetailTimeOnly = computed(() => {
  const match = punchDetailTimeText.value.match(/(\d{2}:\d{2})/)
  return match?.[1] || '--:--'
})

const punchDetailStatusSubtitle = computed(() => {
  if (punchDetailType.value === 'check_in') return `${expectedOutText.value}之后可打下班卡`
  return '今日打卡已完成'
})

const punchDetailStatusTitle = computed(() => {
  const abnormal = selectedDayAbnormalText.value || recordDisplayStatus(activePunchDetailRecord.value)
  if (punchDetailType.value === 'check_out' && isClockOutBeforeExpected(activePunchDetailRecord.value)) return '下班 · 早退'
  if (punchDetailType.value === 'check_out' && abnormal.includes('早退')) return '下班 · 早退'
  if (punchDetailType.value === 'check_in' && abnormal.includes('迟到')) return '上班 · 迟到'
  if (abnormal && abnormal !== '正常' && abnormal !== '-') return abnormal
  return punchDetailType.value === 'check_out' ? '下班 · 正常' : '上班 · 正常'
})

const punchDetailIsAbnormal = computed(() => {
  const title = punchDetailStatusTitle.value
  return isRecordAbnormal(activePunchDetailRecord.value) || ['迟到', '早退', '缺卡', '旷工', '异常'].some((key) => title.includes(key))
})

const punchDetailLocationTitle = computed(() => {
  const record = activePunchDetailRecord.value || {}
  const location = punchDetailType.value === 'check_out' ? record.clock_out_location : record.clock_in_location
  return String(location || reverseAddress.value || runtime.value?.rule?.name || '打卡位置')
})

const punchDetailLocationSub = computed(() => {
  const record = activePunchDetailRecord.value || {}
  const lat = punchDetailType.value === 'check_out' ? record.clock_out_gps_lat : record.clock_in_gps_lat
  const lng = punchDetailType.value === 'check_out' ? record.clock_out_gps_lng : record.clock_in_gps_lng
  const location = punchDetailType.value === 'check_out' ? record.clock_out_location : record.clock_in_location
  if (lat && lng) return `${lat}, ${lng}`
  if (location) return location
  if (geo.lat !== null && geo.lng !== null) return `${geo.lat.toFixed(6)}, ${geo.lng.toFixed(6)}`
  return '暂无坐标'
})

const punchDetailDeviceText = computed(() => {
  const record = activePunchDetailRecord.value || {}
  const backendDevice = punchDetailType.value === 'check_out' ? record.clock_out_device : record.clock_in_device
  if (backendDevice) return String(backendDevice)
  const source = String(activePunchDetailRecord.value?.source || 'app')
  if (source.includes('gate')) return '考勤机'
  if (source.includes('manual')) return '管理员补录'
  return `手机打卡（${navigator.platform || '移动端'}）`
})

const punchDetailRemark = computed(() => {
  return activePunchDetailRecord.value?.correction_note || activePunchDetailRecord.value?.anomaly_type || '无'
})

const clockOutResultRecord = computed(() => selectedPunchDetailRecord.value || todayRecord.value || null)

const clockOutResultStatusText = computed(() => {
  const record = clockOutResultRecord.value
  return [
    formatStatus(record?.status || ''),
    recordDisplayStatus(record),
    String(record?.anomaly_type || ''),
  ].join(' ')
})

const clockOutResultIsEarly = computed(() => {
  const statusText = clockOutResultStatusText.value
  return isClockOutBeforeExpected(clockOutResultRecord.value) || statusText.includes('早退') || statusText.includes('early_leave')
})

const clockOutResultHasOnlyClockInAbnormal = computed(() => {
  const text = clockOutResultStatusText.value
  return text.includes('迟到') && !['早退', '缺卡', '旷工', '打卡位置异常', '不在可打卡时段'].some((key) => text.includes(key))
})

const clockOutResultHasAbnormal = computed(() => {
  if (clockOutResultIsEarly.value) return true
  if (clockOutResultHasOnlyClockInAbnormal.value) return false
  return isRecordAbnormal(clockOutResultRecord.value)
})

const clockOutResultTitle = computed(() => {
  if (clockOutResultIsEarly.value) return '下班 · 早退'
  if (clockOutResultHasAbnormal.value) return '下班 · 异常'
  return '下班 · 正常'
})

const clockOutResultTime = computed(() => {
  const formatted = formatDateTime(clockOutResultRecord.value?.clock_out_time)
  const match = formatted.match(/(\d{2}:\d{2})/)
  return match?.[1] || cstClockText.value
})

const clockOutResultLocation = computed(() => {
  if (isInsideGeofence.value === true) return '公司范围内'
  const ruleName = String(runtime.value?.rule?.name || '').trim()
  if (ruleName) return `${ruleName}范围内`
  return '公司范围内'
})

const dashboardDetails = computed(() => mobileDashboard.value?.details || {})
const dashboardDetailCounts = computed(() => dashboardDetails.value?.counts || {})
const attendanceDetailRows = computed(() => {
  const rows = dashboardDetails.value?.attendance_records
  return Array.isArray(rows) && rows.length ? rows : statsRecords.value
})
const leaveDetailRows = computed<any[]>(() => {
  const rows = dashboardDetails.value?.leave_items
  return Array.isArray(rows) ? rows : []
})
const overtimeDetailRows = computed<any[]>(() => {
  const rows = dashboardDetails.value?.overtime_items
  return Array.isArray(rows) ? rows : []
})

function attendanceFilterMatches(record: any, value: StatsStatusFilter) {
  const status = formatStatus(record?.display_status || record?.status || '')
  const normalized = String(record?.status || '').toLowerCase()
  const abnormal = isRecordAbnormal(record)
  const anomaly = String(record?.anomaly_type || '')
  switch (value) {
    case 'normal':
      return (status === '正常' || normalized === 'normal') && !anomaly && !abnormal
    case 'rest':
    case 'not_scheduled':
      return Boolean(record?.is_rest_day) || ['休息', '未排班'].includes(status)
    case 'abnormal':
      return abnormal
    case 'late':
      return normalized === 'late' || status === '迟到' || anomaly.includes('迟到')
    case 'early_leave':
      return normalized === 'early_leave' || status === '早退' || anomaly.includes('早退')
    case 'missed_clock':
      return normalized === 'missed_clock' || status === '缺卡' || anomaly.includes('缺卡')
    case 'absent':
      return normalized === 'absent' || status === '旷工' || anomaly.includes('旷工')
    default:
      return true
  }
}

function attendanceFilterCount(value: StatsStatusFilter) {
  return attendanceDetailRows.value.filter((record) => attendanceFilterMatches(record, value)).length
}

const filteredStatsRecords = computed(() => {
  return attendanceDetailRows.value.filter((record) => {
    switch (statsStatusFilter.value) {
      case 'normal':
        return attendanceFilterMatches(record, 'normal')
      case 'rest':
      case 'not_scheduled':
        return attendanceFilterMatches(record, 'rest')
      case 'abnormal':
        return attendanceFilterMatches(record, 'abnormal')
      case 'late':
        return attendanceFilterMatches(record, 'late')
      case 'early_leave':
        return attendanceFilterMatches(record, 'early_leave')
      case 'missed_clock':
        return attendanceFilterMatches(record, 'missed_clock')
      case 'absent':
        return attendanceFilterMatches(record, 'absent')
      default:
        return true
    }
  })
})

const statsFilterLabel = computed(() => {
  const option = statsStatusFilterOptions.find((item) => item.value === statsStatusFilter.value)
  return option?.label || '全部'
})

const statsFilterDisplayLabel = computed(() => {
  const label = statsFilterLabel.value
  const count = attendanceFilterCount(statsStatusFilter.value)
  return `${label}(${count}天)`
})

const monthDetailRecords = computed(() => {
  return filteredStatsRecords.value
})

function countByMap(group: 'leave' | 'overtime_date_type' | 'overtime_settlement', key: string) {
  const counts = dashboardDetailCounts.value?.[group] || {}
  return Number(counts?.[key] || 0)
}

function leaveFilterCount(value: LeaveTypeFilter) {
  if (value === 'all') return countByMap('leave', 'all') || leaveDetailRows.value.length
  return countByMap('leave', value) || leaveDetailRows.value.filter((item) => String(item?.type || '') === value).length
}

const leaveFilterDisplayLabel = computed(() => {
  const option = leaveFilterOptions.find((item) => item.value === leaveTypeFilter.value)
  return `${option?.label || '全部'}(${leaveFilterCount(leaveTypeFilter.value)}天)`
})

const filteredLeaveRows = computed(() => {
  const rows = leaveDetailRows.value
  const filtered = leaveTypeFilter.value === 'all'
    ? rows
    : rows.filter((item) => String(item?.type || '') === leaveTypeFilter.value)
  return filtered.slice().sort((a, b) => String(b?.date || '').localeCompare(String(a?.date || '')))
})

function overtimeDateTypeCount(value: OvertimeDateTypeFilter) {
  if (value === 'all') return countByMap('overtime_date_type', 'all') || overtimeDetailRows.value.length
  return countByMap('overtime_date_type', value) || overtimeDetailRows.value.filter((item) => String(item?.date_type || '') === value).length
}

function overtimeSettlementCount(value: OvertimeSettlementFilter) {
  if (value === 'all') return countByMap('overtime_settlement', 'all') || overtimeDetailRows.value.length
  return countByMap('overtime_settlement', value) || overtimeDetailRows.value.filter((item) => String(item?.settlement || '') === value).length
}

const overtimeDateTypeFilterLabel = computed(() => {
  const option = overtimeDateTypeOptions.find((item) => item.value === overtimeDateTypeFilter.value)
  const count = overtimeDateTypeCount(overtimeDateTypeFilter.value)
  return `${option?.label || '全部'}(${count}天)`
})

const overtimeSettlementFilterLabel = computed(() => {
  const option = overtimeSettlementOptions.find((item) => item.value === overtimeSettlementFilter.value)
  const count = overtimeSettlementCount(overtimeSettlementFilter.value)
  return `${option?.label || '全部'}(${count}条)`
})

const applicationSheetDateText = computed(() => {
  const value = String(applicationSheetRecord.value?.date || statsDate.value || todayCst.value)
  const month = Number(value.slice(5, 7))
  const day = Number(value.slice(8, 10))
  if (!month || !day) return value
  return `${month}月${day}号`
})

function leaveRowMeta(item: any) {
  const status = formatStatus(item?.status || '')
  const days = Number(item?.days || 0)
  const minutes = Number(item?.minutes || 0)
  const amount = days > 0 ? `${Number(days.toFixed(1))}天` : minutes > 0 ? `${Math.round(minutes)}分钟` : ''
  return [amount, status !== '-' ? status : '已同步'].filter(Boolean).join(' · ')
}

function openLeaveDetailItem(item: any) {
  if (String(item?.type || '') === 'outside') {
    punchScene.value = 'outside'
    activeMainTab.value = 'punch'
    return
  }
  if (String(item?.type || '') === 'punch_correction') {
    const record = attendanceDetailRows.value.find((row) => String(row?.date || '') === String(item?.date || ''))
    if (record) {
      openApplicationSheet(record)
      return
    }
  }
  showToast(`${formatMonthDay(item?.date)} ${item?.label || '假勤'}：${leaveRowMeta(item)}`, 'primary')
}

const ruleOwnerName = computed(() => auth.user?.name || auth.user?.phone || '员工')
const ruleOwnerInitial = computed(() => String(ruleOwnerName.value || '员').slice(0, 1))
const ruleDisplayName = computed(() => runtime.value?.rule?.name || '上下班打卡')
const runtimeRuleLocations = computed(() => {
  const locations = runtime.value?.rule?.locations
  return Array.isArray(locations) ? locations : []
})
const runtimeRuleWifiNames = computed(() => {
  const names = runtime.value?.rule?.wifi_names
  if (Array.isArray(names)) return names.filter(Boolean).map((item: any) => String(item))
  const legacy = runtime.value?.rule?.wifi_ssid
  return legacy ? [String(legacy)] : []
})
const ruleLocationName = computed(() => {
  const primary = runtimeRuleLocations.value[0] as any
  return primary?.address || primary?.name || runtime.value?.rule?.gps_address || reverseAddress.value || '未配置打卡地点'
})
const ruleWifiName = computed(() => runtimeRuleWifiNames.value.join('、') || '未配置打卡 Wi-Fi')
function normalizeHmText(value: any) {
  const text = String(value || '')
  const match = text.match(/(\d{2}:\d{2})/)
  return match?.[1] || ''
}
const ruleRestText = computed(() => {
  const rows = runtime.value?.rest_periods
  if (!Array.isArray(rows)) return ''
  return rows
    .map((row: any) => {
      const start = Array.isArray(row) ? normalizeHmText(row[0]) : normalizeHmText(row?.start)
      const end = Array.isArray(row) ? normalizeHmText(row[1]) : normalizeHmText(row?.end)
      return start && end ? `${start}-${end}` : ''
    })
    .filter(Boolean)
    .join('、')
})
const ruleTimeDescription = computed(() => {
  const start = expectedInText.value !== '--:--' ? expectedInText.value : '09:30'
  const end = expectedOutText.value !== '--:--' ? expectedOutText.value : '18:30'
  const punchStart = normalizeHmText(runtime.value?.window?.punch_start) || '04:00'
  const punchEnd = normalizeHmText(runtime.value?.window?.punch_end) || '次日03:59'
  const rest = ruleRestText.value ? `${ruleRestText.value}休息，` : ''
  return `${start}-${end}（${rest}可打卡时间${punchStart}-${punchEnd}）`
})

function formatHoursText(hours: any, minutes: any) {
  const numericHours = Number(hours)
  if (Number.isFinite(numericHours) && numericHours > 0) {
    return `${Number(numericHours.toFixed(1))}小时`
  }
  const numericMinutes = Number(minutes || 0)
  if (numericMinutes > 0) return `${Number((numericMinutes / 60).toFixed(1))}小时`
  return '0小时'
}

function normalizeOvertimeTimeText(value: any) {
  const text = String(value || '').trim()
  if (!text) return ''
  const match = text.match(/(\d{1,2}):(\d{2})/)
  if (!match) return text
  return `${match[1].padStart(2, '0')}:${match[2]}`
}

function overtimeTimeRangeText(startTime: any, endTime: any) {
  const start = normalizeOvertimeTimeText(startTime)
  const end = normalizeOvertimeTimeText(endTime)
  if (start && end) return `${start}-${end}`
  return start || end
}

function overtimeSettlementLabel(value: any) {
  const text = String(value || '').trim()
  if (!text || text === '无核算方式') return '未核算'
  if (text.includes('调休')) return '调休'
  if (text.includes('加班费')) return '加班费'
  return text
}

function overtimeSourceLabel(value: any) {
  const source = String(value || '').trim()
  if (source === 'attendance_record') return '上下班打卡'
  if (source === 'overtime_request') return '加班申请'
  if (source === 'approval_instance') return '审批单'
  if (source === 'attendance_effect') return '审批生效'
  return source || '来源记录'
}

function normalizeOvertimeMonthRow(item: any): OvertimeMonthRow | null {
  const year = Number(item?.year || String(item?.date || '').slice(0, 4))
  const month = Number(item?.month || String(item?.date || '').slice(5, 7))
  if (!year || !month) return null
  const hours = Number(item?.hours ?? (Number(item?.minutes || 0) / 60))
  const sourceCount = Number(item?.source_count || 0)
  return {
    id: String(item?.id || `overtime-month:${year}-${String(month).padStart(2, '0')}`),
    year,
    month,
    monthLabel: String(item?.label || `${year}年${month}月`),
    hoursText: formatHoursText(hours, item?.minutes),
    hours: Number.isFinite(hours) ? hours : 0,
    sourceText: sourceCount > 0 ? `${sourceCount}条` : '余额同步',
  }
}

function isOvertimeMonthRow(item: OvertimeMonthRow | null): item is OvertimeMonthRow {
  return item !== null
}

const overtimeMonthRows = computed(() => {
  const rows = dashboardDetails.value?.overtime_month_items
  if (Array.isArray(rows) && rows.length) {
    return rows
      .map(normalizeOvertimeMonthRow)
      .filter(isOvertimeMonthRow)
      .sort((a, b) => (b.year - a.year) || (b.month - a.month))
  }

  const grouped = new Map<string, { id: string; year: number; month: number; label: string; hours: number; minutes: number; source_count: number }>()
  overtimeDetailRows.value
    .filter((item) => String(item?.settlement || '') === 'comp_time')
    .forEach((item) => {
      const year = Number(String(item?.date || '').slice(0, 4))
      const month = Number(String(item?.date || '').slice(5, 7))
      if (!year || !month) return
      const key = `${year}-${String(month).padStart(2, '0')}`
      const current = grouped.get(key) || {
        id: `overtime-month:${key}`,
        year,
        month,
        label: `${year}年${month}月`,
        hours: 0,
        minutes: 0,
        source_count: 0,
      }
      const minutes = Number(item?.minutes || 0)
      const hours = Number(item?.hours ?? (minutes / 60))
      current.hours += Number.isFinite(hours) ? hours : 0
      current.minutes += Number.isFinite(minutes) ? minutes : 0
      current.source_count += 1
      grouped.set(key, current)
    })

  return Array.from(grouped.values())
    .map(normalizeOvertimeMonthRow)
    .filter(isOvertimeMonthRow)
    .sort((a, b) => (b.year - a.year) || (b.month - a.month))
})

const filteredOvertimeRows = computed(() => {
  return overtimeDetailRows.value
    .filter((item) => overtimeDateTypeFilter.value === 'all' || String(item?.date_type || '') === overtimeDateTypeFilter.value)
    .filter((item) => overtimeSettlementFilter.value === 'all' || String(item?.settlement || '') === overtimeSettlementFilter.value)
    .slice()
    .sort((a, b) => String(b?.date || '').localeCompare(String(a?.date || '')))
    .map((item) => ({
      id: String(item?.id || `${item?.date}-${item?.settlement}`),
      date: formatMonthDay(item?.date),
      dateType: String(item?.date_type_label || '工作日'),
      hoursText: formatHoursText(item?.hours, item?.minutes),
      settlementAmountText: overtimeSettlementFilter.value === 'comp_time'
        ? formatHoursText(item?.hours, item?.minutes)
        : `${overtimeSettlementLabel(item?.settlement_label)} ${formatHoursText(item?.hours, item?.minutes)}`,
      sourceText: overtimeSourceLabel(item?.source),
      timeText: overtimeTimeRangeText(item?.start_time, item?.end_time),
    }))
})

const overtimeHoursColumnLabel = computed(() => (
  overtimeSettlementFilter.value === 'comp_time' ? '计为调休' : '核算方式'
))

const applicationRecordCountText = computed(() => {
  const count = Number(mobileDashboard.value?.applications?.record_count || 0)
  return count > 0 ? `${count} 条待处理 ›` : '›'
})

const locationTip = computed(() => {
  if (currentPlaceName.value) return currentPlaceName.value
  if (reverseAddress.value) return reverseAddress.value
  if (geo.lat !== null && geo.lng !== null) {
    const label = geo.source === 'tencent_ip' ? '腾讯IP定位' : 'GPS定位'
    const accuracy = geo.accuracy !== null ? ` 精度约${Math.round(geo.accuracy)}米` : ''
    return `${label} ${geo.lat.toFixed(5)}, ${geo.lng.toFixed(5)}${accuracy}`
  }
  if (geo.error) return `定位失败：${geo.error}`
  return '正在获取定位'
})

const statsHeaderText = computed(() => {
  const text = String(statsDate.value || '')
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return '统计'
  const y = Number(match[1])
  const m = Number(match[2])
  const d = Number(match[3])
  if (statsRange.value === 'month') return `${y}年${m}月`
  if (statsRange.value === 'week') {
    const { start, end } = calcWeekRange(statsDate.value)
    return `${start.slice(5)} - ${end.slice(5)}`
  }
  return `${y}年${m}月${d}日`
})

const statsYearText = computed(() => {
  const text = String(statsDate.value || todayCst.value || '')
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return '2026 年'
  const year = Number(match[1])
  const month = Number(match[2])
  return `${year} 年${statsRange.value === 'day' ? ` ${month} 月` : ''}`
})

const statsPeriodTitle = computed(() => {
  const dashboardRange = mobileDashboard.value?.range
  if (statsRange.value === 'day') return ''
  if (statsRange.value === 'week') {
    const start = String(dashboardRange?.start_date || calcWeekRange(statsDate.value || todayCst.value).start)
    const end = String(dashboardRange?.end_date || calcWeekRange(statsDate.value || todayCst.value).end)
    return `${Number(start.slice(5, 7))}月${Number(start.slice(8, 10))}日- ${Number(end.slice(5, 7))}月${Number(end.slice(8, 10))}日`
  }
  const baseMonth = Number(String(statsDate.value || todayCst.value || '').slice(5, 7) || 0)
  const month = Number(dashboardRange?.month || baseMonth)
  return `${month}月`
})

const statsDayStrip = computed(() => {
  const base = statsDate.value || todayCst.value
  if (!base) return []
  const { start } = weekRangeFromYmd(base)
  const sunday = shiftYmdDays(start, -1)
  const weeks = ['日', '一', '二', '三', '四', '五', '六']
  return Array.from({ length: 7 }, (_, index) => {
    const current = shiftYmdDays(sunday, index)
    return {
      date: current,
      week: weeks[index],
      day: Number(current.slice(8, 10)),
    }
  })
})

const dashboardStats = computed(() => {
  const stats = mobileDashboard.value?.stats || {}
  const abnormalDays = Number(stats.abnormal_days || 0)
  const lateCount = Number(stats.late_count || 0)
  const earlyCount = Number(stats.early_count || 0)
  const missedCount = Number(stats.missed_count || 0)
  const absentCount = Number(stats.absent_count || 0)
  const overtimeHours = stats.overtime_hours === undefined || stats.overtime_hours === null
    ? Number(stats.overtime_minutes || 0) / 60
    : Number(stats.overtime_hours || 0)
  return {
    normal_days: Number(stats.normal_days || 0),
    abnormal_days: abnormalDays,
    late_count: lateCount,
    early_count: earlyCount,
    missed_count: missedCount,
    absent_count: absentCount,
    other_abnormal_count: Math.max(0, abnormalDays - lateCount - earlyCount - missedCount - absentCount),
    leave_count: Number(stats.leave_count || 0),
    correction_count: Number(stats.correction_count || 0),
    outside_count: Number(stats.outside_count || 0),
    business_trip_count: Number(stats.business_trip_count || 0),
    field_work_count: Number(stats.field_work_count || 0),
    overtime_minutes: Number(stats.overtime_minutes || 0),
    overtime_hours: Number.isFinite(overtimeHours) ? overtimeHours : 0,
  }
})

const dashboardOvertimeHoursText = computed(() => {
  const hours = Number(dashboardStats.value.overtime_hours || 0)
  if (!Number.isFinite(hours) || hours <= 0) return '0'
  return String(Number(hours.toFixed(1)))
})

const gaugeAbnormalPercent = computed(() => {
  const total = dashboardStats.value.normal_days + dashboardStats.value.abnormal_days
  if (!total) return 0
  return Math.max(4, Math.min(36, Math.round((dashboardStats.value.abnormal_days / total) * 100)))
})

const dailyCardTitle = computed(() => {
  const minutes = Number(mobileDashboard.value?.daily?.work_minutes || 0)
  if (!minutes) return '上下班打卡（工时）'
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return `上下班打卡（工时 ${hours}小时 ${rest}分钟）`
})

function formatClockOnly(value: any) {
  if (!value) return '-'
  const text = formatDateTime(value)
  const match = text.match(/(\d{2}:\d{2})/)
  return match?.[1] || text
}

const dailyClockInText = computed(() => formatClockOnly(mobileDashboard.value?.daily?.clock_in_time))
const dailyClockOutText = computed(() => formatClockOnly(mobileDashboard.value?.daily?.clock_out_time))
const dailyScheduledInText = computed(() => expectedInText.value !== '--:--' ? expectedInText.value : '09:30')
const dailyScheduledOutText = computed(() => expectedOutText.value !== '--:--' ? expectedOutText.value : '18:30')
const dailyClockInStatusText = computed(() => {
  const status = formatStatus(mobileDashboard.value?.daily?.clock_in_status || todayRecord.value?.clock_in_status || '')
  if (status && status !== '-') return status
  return mobileDashboard.value?.daily?.clock_in_time || todayRecord.value?.clock_in_time ? '正常' : '未打卡'
})
const dailyClockOutStatusText = computed(() => {
  const status = formatStatus(mobileDashboard.value?.daily?.clock_out_status || todayRecord.value?.clock_out_status || '')
  if (status && status !== '-') return status
  return mobileDashboard.value?.daily?.clock_out_time || todayRecord.value?.clock_out_time ? '正常' : '未打卡'
})
const dailyClockInIsAbnormal = computed(() => ['迟到', '缺卡', '旷工'].includes(String(dailyClockInStatusText.value)))
const dailyClockOutIsAbnormal = computed(() => ['早退', '缺卡', '旷工'].includes(String(dailyClockOutStatusText.value)))
const dailyClockOutMissing = computed(() => !(mobileDashboard.value?.daily?.clock_out_time || todayRecord.value?.clock_out_time))
const dailyClockInStatusLine = computed(() => {
  if (dailyClockInText.value === '-') return dailyClockInStatusText.value
  return `${dailyClockInStatusText.value}（${dailyClockInText.value}）`
})
const dailyClockOutStatusLine = computed(() => {
  if (dailyClockOutText.value === '-') return dailyClockOutStatusText.value
  return `${dailyClockOutStatusText.value}（${dailyClockOutText.value}）`
})

const statsSummary = computed(() => {
  const rows = statsRecords.value
  const uniqueDates = new Set(rows.map((x) => x.date)).size
  const actualAttend = rows.filter((x) => ['正常', '迟到', '早退'].includes(String(x.status || ''))).length
  const anomalyCount = rows.filter((x) => ['迟到', '早退', '缺卡', '旷工'].includes(String(x.status || '')) || x.anomaly_type).length
  const overtimeHours = rows.reduce((sum, x) => sum + Number(x.overtime_hours || 0), 0)

  if (statsRange.value === 'month' && monthSummary.value) {
    return {
      shouldAttend: Number(monthSummary.value.work_days || 0),
      actualAttend: Number(monthSummary.value.actual_days || 0),
      anomalyCount: Number(monthSummary.value.anomaly_count || 0),
      overtimeHours:
        Number(monthSummary.value.overtime_weekday_hours || 0)
        + Number(monthSummary.value.overtime_weekend_hours || 0)
        + Number(monthSummary.value.overtime_holiday_hours || 0),
    }
  }

  return {
    shouldAttend: uniqueDates,
    actualAttend,
    anomalyCount,
    overtimeHours,
  }
})

function coordinateCacheKey(latitude: number, longitude: number) {
  return `${latitude.toFixed(5)},${longitude.toFixed(5)}`
}

function mapTencentError(error: any) {
  const detail = error?.response?.data?.detail
  const status = error?.response?.status
  if (status === 404 || detail === 'Not Found') return '腾讯地图接口未就绪，请重启后端服务'
  return detail || error?.message || '腾讯坐标转换失败，范围判断可能有偏差'
}

async function translateGpsToTencent(latitude: number, longitude: number) {
  const cacheKey = coordinateCacheKey(latitude, longitude)
  const cached = tencentCoordCache.get(cacheKey)
  if (cached) return cached
  const result = await get('/attendance/maps/tencent/coord-translate', {
    latitude,
    longitude,
    coord_type: 1,
  })
  const lat = Number(result?.latitude)
  const lng = Number(result?.longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    throw new Error('腾讯地图坐标转换结果异常')
  }
  const converted = { lat, lng }
  tencentCoordCache.set(cacheKey, converted)
  return converted
}

async function loadTencentPlaceName(latitude: number, longitude: number) {
  const cacheKey = coordinateCacheKey(latitude, longitude)
  const cached = tencentAddressCache.get(cacheKey)
  if (cached) {
    currentPlaceName.value = cached.placeName
    reverseAddress.value = cached.address
    return cached
  }
  const result = await get('/attendance/maps/tencent/reverse-geocode', {
    latitude,
    longitude,
  })
  const place = {
    placeName: String(result?.place_name || result?.poi?.title || '').trim(),
    address: String(result?.address || result?.poi?.address || '').trim(),
  }
  tencentAddressCache.set(cacheKey, place)
  currentPlaceName.value = place.placeName
  reverseAddress.value = place.address
  return place
}

function syncTencentPosition(latitude: number, longitude: number) {
  if (!runtime.value?.map_service_enabled) return Promise.resolve(false)
  const cacheKey = coordinateCacheKey(latitude, longitude)
  if (geo.coordinateSystem === 'tencent' && tencentSyncKey === cacheKey) return Promise.resolve(true)
  if (tencentSyncPromise && tencentSyncKey === cacheKey) return tencentSyncPromise

  tencentSyncKey = cacheKey
  geo.tencentSyncing = true
  tencentSyncPromise = translateGpsToTencent(latitude, longitude)
    .then((converted) => {
      geo.lat = converted.lat
      geo.lng = converted.lng
      geo.coordinateSystem = 'tencent'
      geo.transformError = ''
      refreshGeofenceStatus()
      scheduleMapCenterUpdate()
      void loadTencentPlaceName(converted.lat, converted.lng).catch(() => {})
      return true
    })
    .catch((error: any) => {
      geo.transformError = mapTencentError(error)
      return false
    })
    .finally(() => {
      geo.tencentSyncing = false
      tencentSyncPromise = null
    })
  return tencentSyncPromise
}

function setGpsPosition(position: MobileGeoPosition, source: 'gps') {
  const lat = Number(position.coords.latitude)
  const lng = Number(position.coords.longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    throw new Error('定位坐标异常')
  }
  geo.rawLat = lat
  geo.rawLng = lng
  geo.accuracy = Number.isFinite(Number(position.coords.accuracy)) ? Number(position.coords.accuracy) : null
  geo.source = source
  geo.error = ''
  geo.transformError = ''
  const cacheKey = coordinateCacheKey(lat, lng)
  const cached = tencentCoordCache.get(cacheKey)
  if (cached) {
    geo.lat = cached.lat
    geo.lng = cached.lng
    geo.coordinateSystem = 'tencent'
    void loadTencentPlaceName(cached.lat, cached.lng).catch(() => {})
  } else if (geo.coordinateSystem !== 'tencent') {
    geo.lat = lat
    geo.lng = lng
    geo.coordinateSystem = 'wgs84'
  }
  refreshGeofenceStatus()
  scheduleMapCenterUpdate()
  void syncTencentPosition(lat, lng)
}

async function ensureTencentCoordinate() {
  if (!runtime.value?.rule?.require_gps && !(runtime.value?.rule?.locations?.length)) return true
  if (geo.source !== 'gps') return false
  if (geo.coordinateSystem === 'tencent' && geo.lat !== null && geo.lng !== null) return true
  if (geo.rawLat === null || geo.rawLng === null) return false
  const ok = await syncTencentPosition(geo.rawLat, geo.rawLng)
  return ok
}

async function resolveGeo() {
  geo.error = ''
  if (!isMobileGeolocationSupported()) {
    geo.error = '浏览器不支持定位'
    await loadIpLocationFallback()
    return
  }
  try {
    const position = await getCurrentMobilePosition({ enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 })
    setGpsPosition(position, 'gps')
  } catch (error) {
    geo.error = getMobileGeolocationErrorMessage(error)
    await loadIpLocationFallback()
  }
}

async function loadIpLocationFallback() {
  if (!runtime.value?.map_service_enabled) return
  try {
    const result = await get('/attendance/maps/tencent/ip-location')
    const lat = Number(result?.latitude)
    const lng = Number(result?.longitude)
    if (Number.isFinite(lat) && Number.isFinite(lng)) {
      // IP location is only an address hint, never attendance GPS evidence.
      currentPlaceName.value = String(result?.place_name || '').trim()
      if (!reverseAddress.value && result?.address) reverseAddress.value = String(result.address)
    }
  } catch {
    // GPS 失败时再兜底失败，保留原错误文案给用户。
  }
}

function startGeoWatch() {
  if (!isMobileGeolocationSupported() || geoWatchId !== null) return
  const token = ++geoWatchStartToken
  void watchMobilePosition(
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 15000, minimumUpdateInterval: 5000 },
    (position) => {
      setGpsPosition(position, 'gps')
    },
    (error) => {
      geo.error = getMobileGeolocationErrorMessage(error)
      void loadIpLocationFallback().then(() => {
        refreshGeofenceStatus()
        scheduleMapCenterUpdate()
      })
    },
  ).then((watchId) => {
    if (token !== geoWatchStartToken || geoWatchId !== null) {
      void clearMobilePositionWatch(watchId)
      return
    }
    geoWatchId = watchId
  }).catch((error) => {
    if (token === geoWatchStartToken) {
      geo.error = getMobileGeolocationErrorMessage(error)
    }
  })
}

function stopGeoWatch() {
  geoWatchStartToken += 1
  const watchId = geoWatchId
  geoWatchId = null
  void clearMobilePositionWatch(watchId)
}

function chooseMapCenter() {
  const target = configuredPunchTarget()
  if (geo.lat !== null && geo.lng !== null && target) {
    return {
      lat: (geo.lat + target.lat) / 2,
      lng: (geo.lng + target.lng) / 2,
    }
  }
  if (geo.lat !== null && geo.lng !== null) return { lat: geo.lat, lng: geo.lng }
  if (target) return target
  return { lat: 39.9042, lng: 116.4074 }
}

function configuredPunchTarget() {
  const primary = runtimeRuleLocations.value.find((location: any) => {
    const lat = Number(location?.latitude)
    const lng = Number(location?.longitude)
    return Number.isFinite(lat) && Number.isFinite(lng)
  }) as any
  const lat = Number(primary?.latitude ?? runtime.value?.rule?.gps_latitude)
  const lng = Number(primary?.longitude ?? runtime.value?.rule?.gps_longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  return {
    lat,
    lng,
    name: String(primary?.name || primary?.address || runtime.value?.rule?.gps_address || '打卡点'),
    radius: Number(primary?.radius || runtime.value?.rule?.gps_radius_meters || 0),
  }
}

function punchMapZoom() {
  const target = configuredPunchTarget()
  if (!target || geo.lat === null || geo.lng === null) return 18
  const distance = haversineMeters(geo.lat, geo.lng, target.lat, target.lng)
  if (distance <= 250) return 18
  if (distance <= 700) return 17
  if (distance <= 1500) return 16
  if (distance <= 3500) return 15
  if (distance <= 8000) return 14
  if (distance <= 20000) return 13
  return 12
}

async function toggleMapExpanded() {
  mapExpanded.value = !mapExpanded.value
  await nextTick()
  resizePunchMap()
  window.setTimeout(resizePunchMap, 260)
  void renderRealtimeMap()
}

function resizePunchMap() {
  try {
    tencentMap?.resize?.()
  } catch {}
}

function fitPunchMapViewport(TMap: any) {
  if (!tencentMap || !TMap?.LatLng) return
  const target = configuredPunchTarget()
  const hasCurrent = geo.lat !== null && geo.lng !== null
  if (!target || !hasCurrent) {
    const center = chooseMapCenter()
    tencentMap.setCenter?.(new TMap.LatLng(center.lat, center.lng))
    tencentMap.setZoom?.(punchMapZoom())
    return
  }

  const currentLat = Number(geo.lat)
  const currentLng = Number(geo.lng)
  const minLat = Math.min(currentLat, target.lat)
  const maxLat = Math.max(currentLat, target.lat)
  const minLng = Math.min(currentLng, target.lng)
  const maxLng = Math.max(currentLng, target.lng)
  const samePoint = Math.abs(maxLat - minLat) < 0.00001 && Math.abs(maxLng - minLng) < 0.00001
  if (samePoint || !TMap.LatLngBounds || !tencentMap.fitBounds) {
    const center = chooseMapCenter()
    tencentMap.setCenter?.(new TMap.LatLng(center.lat, center.lng))
    tencentMap.setZoom?.(samePoint ? 18 : punchMapZoom())
    return
  }

  const latPadding = Math.max((maxLat - minLat) * 0.18, 0.0008)
  const lngPadding = Math.max((maxLng - minLng) * 0.18, 0.0008)
  const southWest = new TMap.LatLng(minLat - latPadding, minLng - lngPadding)
  const northEast = new TMap.LatLng(maxLat + latPadding, maxLng + lngPadding)
  const bounds = new TMap.LatLngBounds(southWest, northEast)
  try {
    tencentMap.fitBounds(bounds, {
      padding: mapExpanded.value
        ? { top: 32, right: 40, bottom: 42, left: 26 }
        : { top: 22, right: 34, bottom: 34, left: 18 },
    })
  } catch {
    const center = chooseMapCenter()
    tencentMap.setCenter?.(new TMap.LatLng(center.lat, center.lng))
    tencentMap.setZoom?.(punchMapZoom())
  }
}

function markerSvgDataUrl(svg: string) {
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`
}

const currentMarkerIcon = markerSvgDataUrl(`
  <svg xmlns="http://www.w3.org/2000/svg" width="42" height="42" viewBox="0 0 42 42">
    <circle cx="21" cy="21" r="16" fill="#2F7CF6" fill-opacity=".18"/>
    <circle cx="21" cy="21" r="9" fill="#2F7CF6" stroke="#fff" stroke-width="4"/>
  </svg>
`)

const targetMarkerIcon = markerSvgDataUrl(`
  <svg xmlns="http://www.w3.org/2000/svg" width="40" height="48" viewBox="0 0 40 48">
    <path d="M20 46S5 30.6 5 18.9C5 9.6 11.7 3 20 3s15 6.6 15 15.9C35 30.6 20 46 20 46Z" fill="#FF6B57" stroke="#fff" stroke-width="3"/>
    <circle cx="20" cy="19" r="6" fill="#fff"/>
  </svg>
`)

function clearPunchMapOverlays() {
  for (const overlay of [punchMapMarkers, punchMapCircle]) {
    if (!overlay) continue
    try {
      overlay.setMap?.(null)
      overlay.destroy?.()
    } catch {}
  }
  punchMapMarkers = null
  punchMapCircle = null
}

function renderPunchMapOverlays(TMap: any) {
  if (!tencentMap || !TMap?.LatLng) return
  clearPunchMapOverlays()
  const target = configuredPunchTarget()
  const geometries: any[] = []
  if (target) {
    geometries.push({
      id: 'target',
      styleId: 'target',
      position: new TMap.LatLng(target.lat, target.lng),
      properties: { title: target.name },
    })
  }
  if (geo.lat !== null && geo.lng !== null) {
    geometries.push({
      id: 'current',
      styleId: 'current',
      position: new TMap.LatLng(geo.lat, geo.lng),
      properties: { title: currentPlaceName.value || '我的位置' },
    })
  }
  if (geometries.length && TMap.MultiMarker && TMap.MarkerStyle) {
    try {
      punchMapMarkers = new TMap.MultiMarker({
        map: tencentMap,
        styles: {
          current: new TMap.MarkerStyle({
            width: 42,
            height: 42,
            anchor: { x: 21, y: 21 },
            src: currentMarkerIcon,
          }),
          target: new TMap.MarkerStyle({
            width: 40,
            height: 48,
            anchor: { x: 20, y: 46 },
            src: targetMarkerIcon,
          }),
        },
        geometries,
      })
    } catch {}
  }
  if (target?.radius && target.radius > 0 && TMap.MultiCircle && TMap.CircleStyle) {
    try {
      punchMapCircle = new TMap.MultiCircle({
        map: tencentMap,
        styles: {
          targetRadius: new TMap.CircleStyle({
            color: 'rgba(47, 124, 246, 0.12)',
            showBorder: true,
            borderColor: 'rgba(47, 124, 246, 0.55)',
            borderWidth: 2,
          }),
        },
        geometries: [{
          id: 'target-radius',
          styleId: 'targetRadius',
          center: new TMap.LatLng(target.lat, target.lng),
          radius: target.radius,
        }],
      })
    } catch {}
  }
}

async function loadTencentWebSdkConfig() {
  if (tencentMapConfig.value?.key) return tencentMapConfig.value
  const config = await get('/attendance/maps/tencent/web-sdk-config')
  tencentMapConfig.value = {
    key: String(config?.key || ''),
    script_url: String(config?.script_url || 'https://map.qq.com/api/gljs'),
    version: String(config?.version || '1.exp'),
  }
  return tencentMapConfig.value
}

async function loadTencentMapScript() {
  if (window.TMap?.Map) return
  const config = await loadTencentWebSdkConfig()
  if (!config.key) throw new Error('腾讯地图 Key 未配置')
  if (!window.__tencentMapSdkLoading) {
    window.__tencentMapSdkLoading = new Promise<void>((resolve, reject) => {
      const existed = document.querySelector<HTMLScriptElement>('script[data-tencent-map-sdk="gljs"]')
      if (existed) {
        existed.addEventListener('load', () => resolve(), { once: true })
        existed.addEventListener('error', () => reject(new Error('腾讯地图 SDK 加载失败')), { once: true })
        return
      }
      const script = document.createElement('script')
      const params = new URLSearchParams({ v: config.version, key: config.key })
      script.src = `${config.script_url}?${params.toString()}`
      script.async = true
      script.charset = 'utf-8'
      script.dataset.tencentMapSdk = 'gljs'
      script.onload = () => resolve()
      script.onerror = () => reject(new Error('腾讯地图 SDK 加载失败'))
      document.head.appendChild(script)
    })
  }
  await window.__tencentMapSdkLoading
  if (!window.TMap?.Map) throw new Error('腾讯地图 SDK 未就绪')
}

function scheduleMapCenterUpdate() {
  if (mapCenterTimer) window.clearTimeout(mapCenterTimer)
  mapCenterTimer = window.setTimeout(() => {
    void renderRealtimeMap()
  }, 300)
}

async function renderRealtimeMap() {
  if (!runtime.value?.map_browser_enabled) return
  await nextTick()
  if (!mapContainerRef.value) return
  const center = chooseMapCenter()
  try {
    await loadTencentMapScript()
    const TMap = window.TMap
    const position = new TMap.LatLng(center.lat, center.lng)
    if (!tencentMap) {
      tencentMap = new TMap.Map(mapContainerRef.value, {
        center: position,
        zoom: punchMapZoom(),
        pitch: 0,
        rotation: 0,
        viewMode: '2D',
        control: {
          zoom: false,
          scale: true,
          rotation: false,
        },
      })
    }
    renderPunchMapOverlays(TMap)
    fitPunchMapViewport(TMap)
    mapReady.value = true
    mapLoadError.value = ''
  } catch (error: any) {
    mapReady.value = false
    mapLoadError.value = error?.message || '腾讯地图加载失败'
    await loadMapPreview()
  }
}

async function renderAddressPickerMap() {
  if (!runtime.value?.map_browser_enabled) return
  await nextTick()
  if (!addressPickerMapRef.value) return
  const center = selectedOutsidePlace.value
    ? { lat: selectedOutsidePlace.value.latitude, lng: selectedOutsidePlace.value.longitude }
    : chooseMapCenter()
  try {
    await loadTencentMapScript()
    const TMap = window.TMap
    const position = new TMap.LatLng(center.lat, center.lng)
    if (!addressPickerMap) {
      addressPickerMap = new TMap.Map(addressPickerMapRef.value, {
        center: position,
        zoom: 16,
        pitch: 0,
        rotation: 0,
        viewMode: '2D',
      })
    } else {
      addressPickerMap.setCenter(position)
      addressPickerMap.setZoom(16)
    }
  } catch {
    // 地址选择弹层仍可通过列表完成选择，地图失败不阻断业务。
  }
}

async function loadRuntime() {
  try {
    runtime.value = await get('/attendance/my/runtime')
    if (runtime.value?.field_managed) todayRecord.value = runtime.value.current_record
  } catch {
    runtime.value = null
  }
}

async function refreshRuntimePreview() {
  await loadRuntime()
  refreshGeofenceStatus()
}

function refreshGeofenceStatus() {
  const needGeo = !!runtime.value?.rule?.require_gps
  if (!needGeo) {
    isInsideGeofence.value = true
    geofenceDistanceMeters.value = null
    return
  }
  const centerLat = Number(runtime.value?.rule?.gps_latitude)
  const centerLng = Number(runtime.value?.rule?.gps_longitude)
  const radius = Number(runtime.value?.rule?.gps_radius_meters || 0)
  if (!Number.isFinite(centerLat) || !Number.isFinite(centerLng) || geo.lat === null || geo.lng === null) {
    isInsideGeofence.value = null
    geofenceDistanceMeters.value = null
    return
  }
  if (geo.coordinateSystem !== 'tencent') {
    isInsideGeofence.value = null
    geofenceDistanceMeters.value = null
    return
  }
  const dist = haversineMeters(geo.lat, geo.lng, centerLat, centerLng)
  geofenceDistanceMeters.value = dist
  isInsideGeofence.value = radius > 0 ? dist <= radius : true
}

async function loadReverseAddress() {
  if (!runtime.value?.map_service_enabled) return
  if (geo.lat === null || geo.lng === null) return
  try {
    const result = await get('/attendance/maps/tencent/reverse-geocode', {
      latitude: geo.lat,
      longitude: geo.lng,
    })
    reverseAddress.value = String(result?.address || '').trim()
    currentPlaceName.value = String(result?.place_name || result?.poi?.title || '').trim()
  } catch {
    reverseAddress.value = ''
  }
}

function buildCurrentPlaceSuggestion(): PlaceSuggestion | null {
  if (geo.lat === null || geo.lng === null) return null
  return {
    title: (currentPlaceName.value || reverseAddress.value || '当前位置').replace(/^中国/, '').replace(/^北京市/, ''),
    address: reverseAddress.value || `${geo.lat.toFixed(5)}, ${geo.lng.toFixed(5)}`,
    latitude: geo.lat,
    longitude: geo.lng,
  }
}

async function openOutsideAddressPicker() {
  addressPickerVisible.value = true
  addressKeyword.value = selectedOutsidePlace.value?.title || reverseAddress.value || ''
  const current = buildCurrentPlaceSuggestion()
  addressSuggestions.value = current ? [current] : []
  await renderAddressPickerMap()
  await searchOutsidePlaces()
}

async function searchOutsidePlaces() {
  const keyword = addressKeyword.value.trim() || reverseAddress.value
  if (!keyword) {
    const current = buildCurrentPlaceSuggestion()
    addressSuggestions.value = current ? [current] : []
    return
  }
  try {
    const result = await get('/attendance/maps/tencent/place-suggestions', {
      keyword,
      limit: 8,
    })
    const items = Array.isArray(result?.items) ? result.items : []
    const current = buildCurrentPlaceSuggestion()
    addressSuggestions.value = [
      ...(current ? [current] : []),
      ...items.map((item: any) => ({
        title: String(item.title || item.value || ''),
        address: String(item.address || ''),
        latitude: Number(item.latitude),
        longitude: Number(item.longitude),
      })).filter((item: PlaceSuggestion) => item.title && Number.isFinite(item.latitude) && Number.isFinite(item.longitude)),
    ]
  } catch {
    const current = buildCurrentPlaceSuggestion()
    addressSuggestions.value = current ? [current] : []
  }
}

function selectOutsidePlace(item: PlaceSuggestion) {
  selectedOutsidePlace.value = item
  geo.lat = item.latitude
  geo.lng = item.longitude
  geo.rawLat = null
  geo.rawLng = null
  geo.accuracy = null
  geo.coordinateSystem = 'tencent'
  geo.transformError = ''
  currentPlaceName.value = item.title
  reverseAddress.value = item.address || item.title
  refreshGeofenceStatus()
  void renderAddressPickerMap()
  void renderRealtimeMap()
}

function confirmCurrentAddress() {
  if (!selectedOutsidePlace.value) {
    const current = buildCurrentPlaceSuggestion()
    if (current) selectOutsidePlace(current)
  }
  addressPickerVisible.value = false
}

async function loadMapPreview() {
  if (!runtime.value?.map_service_enabled) return
  const center = chooseMapCenter()
  const width = Math.max(320, Math.min(780, Math.floor((window.innerWidth || 390) * 0.9)))
  try {
    const result = await get('/attendance/maps/tencent/static-map-preview', {
      latitude: center.lat,
      longitude: center.lng,
      zoom: 16,
      width,
      height: 176,
    })
    mapPreview.value = String(result?.image_data || '')
  } catch {
    mapPreview.value = ''
  }
}

async function refreshMapData() {
  mapLoading.value = true
  try {
    await Promise.all([loadRuntime(), loadReverseAddress(), loadMapPreview()])
    refreshGeofenceStatus()
    await renderRealtimeMap()
  } finally {
    mapLoading.value = false
  }
}

async function refreshGeoAndMap() {
  await resolveGeo()
  await refreshMapData()
}

async function loadTodayRecord() {
  if (!auth.user?.id) return
  if (runtime.value?.field_managed) { todayRecord.value = runtime.value.current_record; return }
  try {
    const res = await get('/attendance/records', {
      employee_id: auth.user.id,
      start_date: todayCst.value,
      end_date: todayCst.value,
      page: 1,
      page_size: 20,
    })
    const list: any[] = Array.isArray(res?.items) ? res.items : []
    if (!list.length) {
      todayRecord.value = null
      return
    }
    list.sort((a, b) => Number(!!b.clock_out_time) - Number(!!a.clock_out_time))
    todayRecord.value = list[0]
  } catch {
    todayRecord.value = null
  }
}

function calcWeekRange(dateStr: string) {
  return weekRangeFromYmd(dateStr)
}

function calcMonthRange(dateStr: string) {
  return monthRangeFromYmd(dateStr)
}

function currentStatsRange() {
  if (statsRange.value === 'day') {
    return { start: statsDate.value, end: statsDate.value }
  }
  if (statsRange.value === 'week') {
    return calcWeekRange(statsDate.value)
  }
  const { start, end } = calcMonthRange(statsDate.value)
  return { start, end }
}

async function loadStats() {
  if (!auth.user?.id) return
  const { start, end } = currentStatsRange()
  try {
    const recordsRes = await get('/attendance/records', {
      employee_id: auth.user.id,
      start_date: start,
      end_date: end,
      page: 1,
      page_size: 500,
    })
    const items = Array.isArray(recordsRes?.items) ? recordsRes.items : []
    statsRecords.value = items.sort((a: any, b: any) => String(b.date).localeCompare(String(a.date)))
  } catch {
    statsRecords.value = []
  }

  if (statsRange.value === 'month') {
    const { year, month } = calcMonthRange(statsDate.value)
    try {
      monthSummary.value = await get(`/attendance/summaries/${auth.user.id}`, { year, month })
    } catch {
      monthSummary.value = null
    }
  } else {
    monthSummary.value = null
  }
}

async function loadMobileDashboard() {
  try {
    mobileDashboard.value = await get('/attendance/mobile/dashboard', {
      range_type: statsRange.value,
      target_date: statsDate.value || todayCst.value,
    })
  } catch {
    mobileDashboard.value = null
  }
}

function shiftStatsPeriod(step: number) {
  const base = statsDate.value || todayCst.value
  if (!base) return
  if (statsRange.value === 'week') {
    statsDate.value = shiftYmdDays(base, step * 7)
  } else if (statsRange.value === 'month') {
    statsDate.value = shiftYmdMonths(base, step)
  } else {
    statsDate.value = shiftYmdDays(base, step)
  }
}

function openPunchDetailFromRecord(record: any) {
  selectedPunchDetailRecord.value = record
  selectedPunchDetailType.value = recordDisplayStatus(record).includes('早退') ? 'check_out' : 'check_in'
  activeMainTab.value = 'punch_detail'
}

function openPunchDetailFromStats(punchType: 'check_in' | 'check_out' = selectedDayCorrectionType.value) {
  selectedPunchDetailRecord.value = selectedStatsRecord.value || todayRecord.value || null
  selectedPunchDetailType.value = punchType
  activeMainTab.value = 'punch_detail'
}

function resolvePunchCorrectionType(record?: any | null, fallback: 'check_in' | 'check_out' = selectedDayCorrectionType.value) {
  const statusText = [
    recordDisplayStatus(record),
    formatStatus(record?.clock_in_status || ''),
    formatStatus(record?.clock_out_status || ''),
    String(record?.anomaly_type || ''),
  ].join(' ')
  if (statusText.includes('早退') || statusText.includes('下班')) return 'check_out'
  if (statusText.includes('迟到') || statusText.includes('上班')) return 'check_in'
  if (record) {
    const hasClockIn = Boolean(record.clock_in_time)
    const hasClockOut = Boolean(record.clock_out_time)
    if (!hasClockIn && hasClockOut) return 'check_in'
    if (hasClockIn && !hasClockOut) return 'check_out'
  }
  return fallback
}

function openPunchCorrectionFromDetail() {
  const targetRecord = activePunchDetailRecord.value || selectedStatsRecord.value || todayRecord.value || null
  void router.push({
    path: '/app/approval/start',
    query: attendanceApplicationQuery('punch_correction', targetRecord, punchDetailType.value),
  })
}

function openLeaveApplicationFromDetail() {
  const targetRecord = activePunchDetailRecord.value || selectedStatsRecord.value || todayRecord.value || null
  void router.push({
    path: '/app/leave',
    query: attendanceApplicationQuery('leave', targetRecord),
  })
}

function openClockOutResult(record?: any | null) {
  selectedPunchDetailRecord.value = record || todayRecord.value || null
  selectedPunchDetailType.value = 'check_out'
  activeMainTab.value = 'clock_out_result'
}

function openClockInResult(record?: any | null) {
  selectedPunchDetailRecord.value = record || todayRecord.value || null
  selectedPunchDetailType.value = 'check_in'
  activeMainTab.value = 'punch_detail'
}

function openExistingClockOutResultIfNeeded() {
  if (activeMainTab.value !== 'punch') return
  const record = todayRecord.value
  if (!record?.clock_out_time) return
  selectedPunchDetailRecord.value = record
  selectedPunchDetailType.value = 'check_out'
}

function openClockOutUpdatePage() {
  activeMainTab.value = 'punch'
  punchScene.value = 'normal'
  void nextTick(() => {
    scheduleMapCenterUpdate()
  })
}

function openRuleDetail() {
  activeMainTab.value = 'rule_detail'
}

function toggleRuleSection(key: keyof typeof openedRuleSections) {
  openedRuleSections[key] = !openedRuleSections[key]
}

function openApplicationSheet(record: any) {
  applicationSheetRecord.value = record
  applicationSheetVisible.value = true
}

function attendanceApplicationQuery(key: string, record?: any | null, punchTypeOverride?: 'check_in' | 'check_out') {
  const targetRecord = record || applicationSheetRecord.value || selectedPunchDetailRecord.value || selectedStatsRecord.value || todayRecord.value
  const statusText = recordDisplayStatus(targetRecord)
  const dateText = String(targetRecord?.date || statsDate.value || todayCst.value)
  const query: Record<string, string> = {
    business_code: key,
    source: 'attendance_abnormal',
    attendance_date: dateText,
    ...attendanceApprovalReturnQuery,
  }
  if (statusText && statusText !== '-') query.attendance_status = statusText
  if (key === 'punch_correction') {
    query.punch_type = punchTypeOverride || resolvePunchCorrectionType(targetRecord)
  }
  return query
}

function chooseApplicationAction(key: string) {
  applicationSheetVisible.value = false
  if (key === 'leave') {
    void router.push({ path: '/app/leave', query: attendanceApplicationQuery(key) })
    return
  }
  if (key === 'outside') {
    activeMainTab.value = 'punch'
    punchScene.value = 'outside'
    return
  }
  void router.push({ path: '/app/approval/start', query: attendanceApplicationQuery(key) })
}

async function confirmMonthlyAttendance() {
  monthlyConfirmSubmitting.value = true
  try {
    if (statsRange.value !== 'month') {
      statsRange.value = 'month'
    }
    await loadStats()
    const summary = monthSummary.value
    if (!summary?.id) {
      showToast('暂无可确认的月度考勤汇总，请稍后重试', 'warning')
      return
    }
    const status = String(summary.status || '').toLowerCase()
    if (status === 'confirmed' || status === 'locked') {
      showToast(status === 'locked' ? '本月考勤已锁定' : '本月考勤已确认', 'success')
      return
    }
    monthSummary.value = await post('/attendance/summaries/confirm', { summary_id: summary.id })
    await Promise.all([loadStats(), loadMobileDashboard()])
    showToast('已确认本月考勤结果无误', 'success')
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '确认考勤汇总失败', 'danger')
  } finally {
    monthlyConfirmSubmitting.value = false
  }
}

function selectStatsStatusFilter(value: StatsStatusFilter) {
  statsStatusFilter.value = value
  statusFilterVisible.value = false
}

function selectLeaveTypeFilter(value: LeaveTypeFilter) {
  leaveTypeFilter.value = value
  leaveFilterVisible.value = false
}

function selectOvertimeDateTypeFilter(value: OvertimeDateTypeFilter) {
  overtimeDateTypeFilter.value = value
  overtimeDateTypeFilterVisible.value = false
}

function selectOvertimeSettlementFilter(value: OvertimeSettlementFilter) {
  overtimeSettlementFilter.value = value
  overtimeSettlementFilterVisible.value = false
}

function selectExportStatusFilter(value: ExportStatusFilter) {
  exportStatusFilter.value = value
  exportStatusPickerVisible.value = false
}

function selectExportRange(value: 'day' | 'week' | 'month') {
  statsRange.value = value
  exportRangePickerVisible.value = false
}

function openAttendanceStatsFromRule() {
  statsRange.value = 'day'
  statsDate.value = todayCst.value
  activeMainTab.value = 'stats'
  void loadStats()
  void loadMobileDashboard()
}

function openAttendanceSummaryFromStats(
  tab: MonthDetailTab = 'attendance',
  statusOrLeaveFilter?: StatsStatusFilter | LeaveTypeFilter,
  overtimeDateFilter?: OvertimeDateTypeFilter,
  overtimeSettlement?: OvertimeSettlementFilter,
) {
  if (!statsDate.value) statsDate.value = todayCst.value
  if (statsRange.value === 'day') statsRange.value = 'month'
  monthDetailTab.value = tab
  if (tab === 'attendance') {
    statsStatusFilter.value = (statusOrLeaveFilter || 'all') as StatsStatusFilter
  }
  if (tab === 'leave') {
    leaveTypeFilter.value = (statusOrLeaveFilter || 'all') as LeaveTypeFilter
  }
  if (tab === 'overtime') {
    overtimeDateTypeFilter.value = overtimeDateFilter || 'all'
    overtimeSettlementFilter.value = overtimeSettlement || 'all'
  }
  activeMainTab.value = 'month_detail'
  void Promise.all([loadStats(), loadMobileDashboard()])
}

function openApplyItem(template: AttendanceApprovalTemplateTile) {
  if (!template.source) {
    showToast('该申请模板暂未启用，请联系 HR 或管理员', 'warning')
    return
  }
  void router.push({
    path: '/app/approval/start',
    query: {
      business_code: template.businessCode,
      source: 'attendance_apply',
      ...attendanceApprovalReturnQuery,
    },
  })
}

function openAttendanceApplyCenter() {
  activeMainTab.value = 'apply'
  void loadAttendanceApprovalTemplates()
}

function openTodayAbnormal() {
  statsRange.value = 'month'
  statsDate.value = todayCst.value
  monthDetailTab.value = 'attendance'
  statsStatusFilter.value = 'abnormal'
  activeMainTab.value = 'month_detail'
  void loadStats()
  void loadMobileDashboard()
}

function buildOutsidePhotoMeta() {
  if (outsidePhotoUrl.value) {
    return outsidePhotoUrl.value
  }
  const parts = [
    `scene=${punchScene.value}`,
    'clock_type=field',
    outsideClient.value.trim() ? `client=${outsideClient.value.trim()}` : '',
    outsideRemark.value.trim() ? `remark=${outsideRemark.value.trim()}` : '',
    reverseAddress.value ? `address=${reverseAddress.value}` : '',
    geo.lat !== null ? `lat=${geo.lat}` : '',
    geo.lng !== null ? `lng=${geo.lng}` : '',
    `photo=${outsidePhotoReady.value ? 'ready' : 'pending'}`,
  ].filter(Boolean)
  return `outside:${parts.join(';')}`.slice(0, 500)
}

function buildDevicePayload() {
  const nav = window.navigator
  const ua = String(nav.userAgent || '')
  const platform = String(nav.platform || '')
  const uaData: any = (nav as any).userAgentData
  const brand = Array.isArray(uaData?.brands) && uaData.brands.length ? String(uaData.brands[0]?.brand || '') : ''
  const model = String(uaData?.model || '')
  const mobileHint = typeof uaData?.mobile === 'boolean' ? uaData.mobile : /Mobile|Android|iPhone|iPad/i.test(ua)
  const os =
    /Android/i.test(ua) ? 'Android'
      : /iPhone|iPad|iOS/i.test(ua) ? 'iOS'
        : /Harmony/i.test(ua) ? 'HarmonyOS'
          : /Windows/i.test(ua) ? 'Windows'
            : /Mac OS|Macintosh/i.test(ua) ? 'macOS'
              : /Linux/i.test(ua) ? 'Linux'
                : '未知系统'
  const deviceName = [brand, platform].filter(Boolean).join(' ').trim() || undefined
  return {
    device_name: deviceName,
    device_model: model || undefined,
    device_os: os,
    device_id: mobileHint ? `${os}-${platform || 'mobile'}`.slice(0, 120) : undefined,
  }
}

function fileToImage(file: File) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const image = new Image()
    image.onload = () => {
      URL.revokeObjectURL(url)
      resolve(image)
    }
    image.onerror = reject
    image.src = url
  })
}

async function buildWatermarkedPhoto(file: File) {
  const image = await fileToImage(file)
  const maxWidth = 1280
  const scale = Math.min(1, maxWidth / image.width)
  const width = Math.max(1, Math.round(image.width * scale))
  const height = Math.max(1, Math.round(image.height * scale))
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('当前浏览器不支持水印处理')
  ctx.drawImage(image, 0, 0, width, height)
  const lines = [
    `外出打卡 ${cstClockText.value}`,
    outsidePlaceTitle.value.replace(/›$/, ''),
    outsidePlaceAddress.value,
    geo.lat !== null && geo.lng !== null ? `GPS ${geo.lat.toFixed(5)}, ${geo.lng.toFixed(5)}` : '',
    outsideClient.value.trim() ? `客户 ${outsideClient.value.trim()}` : '',
  ].filter(Boolean)
  const padding = Math.max(18, Math.round(width * 0.025))
  const lineHeight = Math.max(28, Math.round(width * 0.035))
  const boxHeight = padding * 2 + lineHeight * lines.length
  ctx.fillStyle = 'rgba(15, 23, 42, 0.58)'
  ctx.fillRect(0, height - boxHeight, width, boxHeight)
  ctx.fillStyle = '#fff'
  ctx.font = `${Math.max(20, Math.round(width * 0.026))}px sans-serif`
  lines.forEach((line, index) => {
    ctx.fillText(line, padding, height - boxHeight + padding + lineHeight * (index + 0.8))
  })
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((item) => item ? resolve(item) : reject(new Error('水印照片生成失败')), 'image/jpeg', 0.86)
  })
  return {
    blob,
    preview: canvas.toDataURL('image/jpeg', 0.86),
  }
}

function markOutsidePhoto() {
  outsidePhotoInputRef.value?.click()
}

async function handleOutsidePhotoChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  outsidePhotoUploading.value = true
  try {
    const watermarked = await buildWatermarkedPhoto(file)
    outsidePhotoPreview.value = watermarked.preview
    const formData = new FormData()
    formData.append('file', watermarked.blob, `attendance-watermark-${Date.now()}.jpg`)
    const uploaded = await post<{ url: string; filename: string; size: number }>('/upload?category=attendance', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    outsidePhotoReady.value = true
    outsidePhotoUrl.value = uploaded.url
    showToast('水印照片已上传，可点击缩略图查看', 'success')
  } catch (error: any) {
    outsidePhotoReady.value = false
    outsidePhotoUrl.value = ''
    showToast(error?.response?.data?.detail || error?.message || '水印照片上传失败', 'danger')
  } finally {
    outsidePhotoUploading.value = false
    input.value = ''
  }
}

async function handlePunch(endpoint: '/attendance/clock-in' | '/attendance/clock-out') {
  if (punchSubmitting.value) return
  const previousContext = runtime.value?.project_context
  await loadRuntime()
  if (!runtime.value?.has_rule) { showToast('尚未分配有效项目或考勤规则', 'warning'); return }
  if (runtime.value?.field_managed && previousContext !== runtime.value.project_context) { showToast('项目或规则已更新，请确认后重新打卡', 'warning'); return }
  const photoRequired = punchScene.value === 'outside'
    ? runtime.value?.rule?.watermark_photo_required
    : runtime.value?.rule?.require_photo
  if (photoRequired && !outsidePhotoReady.value) { showToast('请先拍摄打卡照片', 'warning'); return }
  punchSubmitting.value = true
  try {
    if (geo.lat === null || geo.lng === null) {
      await resolveGeo()
      refreshGeofenceStatus()
    }
    if (!(await ensureTencentCoordinate())) {
      showToast('GPS 坐标还未完成腾讯地图转换，请稍后重试', 'warning')
      return
    }
    const punchLat = geo.lat
    const punchLng = geo.lng
    let punchPlaceName = ''
    let punchAddress = ''
    if (punchLat !== null && punchLng !== null && runtime.value?.map_service_enabled) {
      try {
        const place = await loadTencentPlaceName(punchLat, punchLng)
        punchPlaceName = place.placeName
        punchAddress = place.address
      } catch {
        // 地点解析暂时失败时仍保留真实坐标，不用规则名称充当位置。
      }
    }
    const gpsLocationText = punchLat !== null && punchLng !== null
      ? `GPS ${punchLat.toFixed(6)}, ${punchLng.toFixed(6)}`
      : ''
    const payload: any = {
      source: 'app',
      project_context: runtime.value?.project_context,
      gps_latitude: punchLat,
      gps_longitude: punchLng,
      ...buildDevicePayload(),
      location_name: (
        punchScene.value === 'outside'
          ? (selectedOutsidePlace.value?.title || outsideClient.value || punchPlaceName || gpsLocationText)
          : (punchPlaceName || punchAddress || gpsLocationText)
      ) || undefined,
      location_address: (
        punchScene.value === 'outside'
          ? (selectedOutsidePlace.value?.address || punchAddress || '')
          : punchAddress
      ) || undefined,
      photo_url: outsidePhotoUrl.value || undefined,
    }
    const record = await post(endpoint, payload)
    outsidePhotoReady.value = false
    outsidePhotoUrl.value = ''
    outsidePhotoPreview.value = ''
    await loadRuntime()
    await Promise.all([loadTodayRecord(), loadStats(), loadMobileDashboard(), refreshMapData()])
    const actionText = endpoint === '/attendance/clock-in' ? '上班打卡成功' : '下班打卡成功'
    const latestRecord = todayRecord.value || record
    const statusText = endpoint === '/attendance/clock-out'
      ? String(mobileDashboard.value?.daily?.clock_out_status || '').trim()
      : formatStatus(latestRecord?.display_status || latestRecord?.status || '')
    const toastColor = ['迟到', '早退', '缺卡', '旷工', '打卡位置异常', '异常'].includes(statusText) ? 'warning' : 'success'
    showToast(statusText && statusText !== '-' ? `${actionText}，状态：${statusText}` : actionText, toastColor)
    selectedPunchDetailRecord.value = latestRecord
    selectedPunchDetailType.value = endpoint === '/attendance/clock-out' ? 'check_out' : 'check_in'
    activeMainTab.value = 'punch'
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '打卡失败，请稍后重试', 'danger')
  } finally {
    punchSubmitting.value = false
  }
}

async function validateCurrentPunch() {
  await loadRuntime()
  await loadTodayRecord()
  if (!runtime.value?.has_rule) throw new Error('尚未分配有效项目或考勤规则')
  if (geo.lat === null || geo.lng === null) {
    await resolveGeo()
  }
  if (!(await ensureTencentCoordinate())) {
    showToast('GPS 坐标还未完成腾讯地图转换，请稍后重试', 'warning')
    throw new Error('腾讯地图坐标转换未完成')
  }
  refreshGeofenceStatus()
  const validation = await post('/attendance/clock/validate', {
    phase: punchPhase.value === 'done' ? 'clock_out' : punchPhase.value,
    target_date: todayCst.value,
    punch_time: new Date().toISOString(),
    source: 'app',
    gps_latitude: geo.lat,
    gps_longitude: geo.lng,
    photo_url: outsidePhotoUrl.value || undefined,
  })
  if (runtime.value) {
    runtime.value = {
      ...runtime.value,
      punch_preview: validation,
    }
  }
  return validation
}

async function handleMainPunch() {
  let validation: any | null = null
  try {
    validation = await validateCurrentPunch()
  } catch (error: any) {
    showToast(error?.response?.data?.detail || error?.message || '打卡规则校验失败，请稍后重试', 'danger')
    return
  }
  if (isEarlyLeavePunch(validation)) {
    const ok = await confirmEarlyLeavePunch()
    if (!ok) return
  } else if (validation && validation.is_valid === false) {
    const ok = await confirmAbnormalPunch(validation)
    if (!ok) return
  }
  if (punchPhase.value === 'clock_in') {
    await handlePunch('/attendance/clock-in')
    return
  }
  if (punchPhase.value === 'clock_out') {
    await handlePunch('/attendance/clock-out')
    return
  }
  await handlePunch('/attendance/clock-out')
}

async function handleOutsidePunch() {
  if (!outsideClient.value.trim()) {
    showToast('请先添加拜访客户', 'warning')
    return
  }
  if (punchPhase.value === 'clock_in') {
    await handlePunch('/attendance/clock-in')
    return
  }
  await handlePunch('/attendance/clock-out')
}

async function submitOutsideApproval() {
  const trimmedClient = outsideClient.value.trim()
  const trimmedRemark = outsideRemark.value.trim()
  if (!trimmedClient) {
    showToast('请先添加拜访客户', 'warning')
    return
  }
  if (!trimmedRemark) {
    showToast('请先填写外出审批事由', 'warning')
    return
  }
  if (!outsidePhotoReady.value) {
    showToast('请先拍摄水印照片', 'warning')
    return
  }
  if (geo.lat === null || geo.lng === null) {
    await resolveGeo()
  }
  if (!(await ensureTencentCoordinate())) {
    showToast('GPS 坐标还未完成腾讯地图转换，请稍后重试', 'warning')
    return
  }
  if (geo.lat === null || geo.lng === null) {
    showToast('未获取到 GPS 定位，请刷新定位后再提交审批', 'warning')
    return
  }
  punchSubmitting.value = true
  try {
    await post('/attendance/outside-approval', {
      address: outsidePlaceAddress.value || reverseAddress.value || outsidePlaceTitle.value,
      place_title: outsidePlaceTitle.value,
      latitude: geo.lat,
      longitude: geo.lng,
      client_name: trimmedClient,
      remark: trimmedRemark,
      photo_url: buildOutsidePhotoMeta(),
      occurred_at: new Date().toISOString(),
    })
    await loadMobileDashboard()
    showToast('外出打卡审批已提交', 'success')
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '提交审批失败', 'danger')
  } finally {
    punchSubmitting.value = false
  }
}

const exportStatusLabel = computed(() => exportStatusOptions.find((item) => item.value === exportStatusFilter.value)?.label || '全部')
const exportRangeLabel = computed(() => {
  const range = currentStatsRange()
  if (statsRange.value === 'month') {
    return `${Number(range.start.slice(5, 7))}月${Number(range.start.slice(8, 10))}日-${Number(range.end.slice(5, 7))}月${Number(range.end.slice(8, 10))}日(本月)`
  }
  if (statsRange.value === 'week') {
    return `${Number(range.start.slice(5, 7))}月${Number(range.start.slice(8, 10))}日-${Number(range.end.slice(5, 7))}月${Number(range.end.slice(8, 10))}日`
  }
  return `${Number(range.start.slice(5, 7))}月${Number(range.start.slice(8, 10))}日`
})
const exportOwnerName = computed(() => auth.user?.name || auth.user?.phone || '当前员工')

function openExportReportPage() {
  if (statsRange.value === 'day') statsRange.value = 'month'
  activeMainTab.value = 'export_report'
}

async function downloadCurrentStats() {
  statsExporting.value = true
  try {
    const blob = await get<Blob>('/attendance/mobile/export', {
      range_type: statsRange.value,
      target_date: statsDate.value || todayCst.value,
      status_filter: exportStatusFilter.value,
    }, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `attendance-${statsRange.value}-${statsDate.value || todayCst.value}.csv`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    showToast('考勤报表已导出', 'success')
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '导出报表失败，请稍后重试', 'danger')
  } finally {
    statsExporting.value = false
  }
}

async function exportCurrentStats() {
  await downloadCurrentStats()
}

function handleStatsDateChange(event: CustomEvent) {
  const value = String(event.detail.value || '')
  if (!value) return
  statsDate.value = value.slice(0, 10)
}

watch([statsRange, statsDate], () => {
  void loadStats()
  void loadMobileDashboard()
})

watch(
  () => [geo.lat, geo.lng, runtime.value?.rule?.gps_latitude, runtime.value?.rule?.gps_longitude, runtime.value?.rule?.gps_radius_meters],
  () => {
    refreshGeofenceStatus()
    scheduleMapCenterUpdate()
  },
)

watch(punchScene, () => {
  void renderRealtimeMap()
})

watch(activeMainTab, (tab) => {
  if (tab === 'apply') {
    void loadAttendanceApprovalTemplates()
  }
})

watch(
  () => route.query.tab,
  () => {
    syncAttendanceTabFromRoute()
  },
)

function clearAttendanceTimers() {
  if (clockTimer) {
    window.clearInterval(clockTimer)
    clockTimer = null
  }
  if (runtimeRefreshTimer) {
    window.clearInterval(runtimeRefreshTimer)
    runtimeRefreshTimer = null
  }
  if (earlyLeaveDialog.open) {
    resolveEarlyLeaveDialog(false)
  }
  if (outOfRangeDialog.open) {
    resolveOutOfRangeDialog(false)
  }
}

onIonViewWillEnter(async () => {
  setGlobalTabbarVisible(false)
  syncAttendanceTabFromRoute()
  if (!statsDate.value) {
    statsDate.value = todayCst.value
  }
  updateClockText()
  clearAttendanceTimers()
  clockTimer = window.setInterval(updateClockText, 1000)
  runtimeRefreshTimer = window.setInterval(() => {
    void refreshRuntimePreview()
  }, 15000)

  await loadRuntime()
  await Promise.all([resolveGeo(), loadTodayRecord(), loadStats(), loadMobileDashboard()])
  startGeoWatch()
  await Promise.all([loadReverseAddress(), loadMapPreview(), renderRealtimeMap()])
  refreshGeofenceStatus()
  openExistingClockOutResultIfNeeded()
})

onIonViewDidLeave(() => {
  setGlobalTabbarVisible(true)
  clearAttendanceTimers()
  stopGeoWatch()
})

onBeforeUnmount(() => {
  setGlobalTabbarVisible(true)
  clearAttendanceTimers()
  if (mapCenterTimer) {
    window.clearTimeout(mapCenterTimer)
    mapCenterTimer = null
  }
  stopGeoWatch()
  clearPunchMapOverlays()
  if (tencentMap?.destroy) {
    tencentMap.destroy()
    tencentMap = null
  }
  if (addressPickerMap?.destroy) {
    addressPickerMap.destroy()
    addressPickerMap = null
  }
})
</script>

<style scoped>
.attendance-shell {
  width: min(100%, var(--mobile-window-width));
  min-height: 100%;
  margin: 0 auto;
  padding: 0 10px 92px;
  background: #f1f4f8;
}

.attendance-native-head {
  position: sticky;
  top: 0;
  z-index: 5;
  display: grid;
  grid-template-columns: 42px 1fr 42px;
  align-items: center;
  height: 72px;
  padding-top: 12px;
  background: #f1f4f8;
}

.attendance-native-head h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  text-align: center;
  color: #111827;
}

.attendance-native-head h1 span,
.attendance-native-head h1 small {
  display: block;
}

.attendance-native-head h1 small {
  margin-top: 2px;
  color: #6b7280;
  font-size: 11px;
  font-weight: 500;
}

.head-icon-btn {
  width: 42px;
  height: 42px;
  border: 0;
  background: transparent;
  color: #111827;
  font-size: 30px;
  line-height: 1;
}

.head-icon-btn:last-child {
  font-size: 24px;
}

.refresh-chip {
  border: 0;
  cursor: pointer;
  background: #f2f7ff;
}

.attendance-main-segment {
  --background: #fff;
  border-radius: 4px;
  padding: 4px;
}

.punch-panel {
  padding: 10px 10px 18px;
  border-radius: 8px;
  box-shadow: none;
}

.subtab-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  margin-bottom: 8px;
  border-radius: 4px;
  background: #f6f7fa;
  overflow: hidden;
}

.subtab-pill {
  min-height: 32px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: #506178;
  font-size: 14px;
  font-weight: 700;
}

.subtab-pill.is-active {
  background: #ffffff;
  color: #111827;
}

.map-surface {
  position: relative;
  isolation: isolate;
  height: 132px;
  border-radius: 4px 4px 0 0;
  overflow: hidden;
  border: 0;
  background: #eef3f9;
  transition: height 0.22s ease;
}

.map-surface.is-expanded {
  height: 312px;
}

.map-image {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.tencent-map-container {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 0;
}

.tencent-map-container :deep([class*="compass"]),
.tencent-map-container :deep([class*="Compass"]),
.tencent-map-container :deep([class*="rotate"]),
.tencent-map-container :deep([class*="Rotate"]) {
  top: 6px !important;
  right: 7px !important;
  transform: scale(0.58) !important;
  transform-origin: right top !important;
}

.tencent-map-container :deep([class*="zoom"]),
.tencent-map-container :deep([class*="Zoom"]),
.tencent-map-container :deep([title*="放大"]),
.tencent-map-container :deep([aria-label*="放大"]) {
  display: none !important;
}

.map-loading-state {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #496491;
  font-size: 12px;
  background: rgba(238, 243, 249, 0.72);
}

.map-avatar-dot {
  position: absolute;
  left: 50%;
  top: 42%;
  transform: translate(-50%, -50%);
  z-index: 2;
  width: 46px;
  height: 46px;
  border-radius: 999px;
  background: #f0f4fb;
  border: 2px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 8px 16px rgba(24, 39, 75, 0.18);
  color: #2e3f5d;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.map-avatar-dot.is-loading {
  position: static;
  transform: none;
}

.map-compact-compass {
  position: absolute;
  top: 7px;
  right: 8px;
  z-index: 4;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 5px 13px rgba(15, 23, 42, 0.15);
  color: #ef4444;
  font-size: 10px;
  font-weight: 900;
  pointer-events: none;
}

.map-compact-compass::before {
  content: '';
  position: absolute;
  top: 4px;
  left: 50%;
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 13px solid #ef4444;
  transform: translateX(-50%);
}

.map-compact-compass span {
  position: relative;
  z-index: 1;
  margin-top: 10px;
}

.map-legend {
  position: absolute;
  left: 7px;
  bottom: 6px;
  z-index: 3;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.12);
  color: #334155;
  font-size: 10px;
  font-weight: 700;
  backdrop-filter: blur(8px);
}

.map-legend span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.map-legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  box-shadow: 0 0 0 2px #fff;
}

.map-legend-dot.is-current {
  background: #2F7CF6;
}

.map-legend-dot.is-target {
  background: #FF6B57;
}

.map-expand-btn {
  position: absolute;
  right: 10px;
  bottom: 10px;
  z-index: 9999;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 9px;
  padding: 0;
  background: rgba(17, 24, 39, 0.82);
  color: #fff;
  box-shadow: 0 5px 12px rgba(15, 23, 42, 0.2);
  backdrop-filter: blur(8px);
  cursor: pointer;
  pointer-events: auto;
  touch-action: manipulation;
}

.map-expand-btn:active {
  transform: scale(0.96);
}

.map-expand-icon {
  position: relative;
  display: block;
  width: 14px;
  height: 14px;
}

.map-expand-icon::before,
.map-expand-icon::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  background: currentColor;
  border-radius: 999px;
  transform: translate(-50%, -50%);
}

.map-expand-icon::before {
  width: 14px;
  height: 2px;
}

.map-expand-icon::after {
  width: 2px;
  height: 14px;
}

.map-expand-icon i {
  position: absolute;
  width: 5px;
  height: 5px;
  border-color: currentColor;
}

.map-expand-icon i:nth-child(1) {
  left: 0;
  top: 0;
  border-left: 2px solid;
  border-top: 2px solid;
}

.map-expand-icon i:nth-child(2) {
  right: 0;
  top: 0;
  border-right: 2px solid;
  border-top: 2px solid;
}

.map-expand-icon i:nth-child(3) {
  left: 0;
  bottom: 0;
  border-left: 2px solid;
  border-bottom: 2px solid;
}

.map-expand-icon i:nth-child(4) {
  right: 0;
  bottom: 0;
  border-right: 2px solid;
  border-bottom: 2px solid;
}

.map-expand-icon.is-expanded {
  transform: rotate(45deg);
}

.range-status-picker {
  display: block;
  width: 100%;
  border: 0;
  padding: 0 12px;
  background: transparent;
  cursor: pointer;
}

.range-status-main {
  display: block;
  margin-top: 10px;
  text-align: center;
  font-size: 22px;
  line-height: 1.35;
  font-weight: 700;
  color: #2f8f4f;
}

.range-status-picker .range-status-main {
  color: #b86a12;
}

.range-status-main.is-warning {
  color: #c36d08;
}

.range-status-sub {
  display: block;
  margin-top: 2px;
  text-align: center;
  color: #6b7280;
  font-size: 12px;
}

.punch-state-banner {
  min-height: 28px;
  margin: 12px 10px 0;
  padding: 7px 10px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  font-size: 12px;
  font-weight: 700;
  color: #267347;
  background: #edf8f1;
}

.punch-state-banner.is-late,
.punch-state-banner.is-early,
.punch-state-banner.is-pending {
  color: #a35d00;
  background: #fff6df;
}

.punch-state-banner.is-danger {
  color: #b42318;
  background: #fff1f0;
}

.punch-state-banner.is-done {
  color: #64748b;
  background: #f1f5f9;
}

.punch-state-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: currentColor;
}

.punch-circle-btn {
  width: 126px;
  height: 126px;
  margin: 124px auto 0;
  border-radius: 999px;
  border: 7px solid #36b774;
  background: #fbfffc;
  color: #1b2d47;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  position: relative;
  overflow: visible;
}

.punch-circle-btn.is-late,
.punch-circle-btn.is-early,
.punch-circle-btn.is-pending {
  border-color: #e8c241;
  background: #fffdf4;
}

.punch-circle-btn.is-danger {
  border-color: #ff8a80;
  background: #fff8f7;
}

.punch-circle-btn.is-done {
  border-color: #cbd5e1;
  background: #f8fafc;
  color: #64748b;
}

.punch-circle-btn.is-clock-out {
  border-color: #e8c241;
  background: #fffdf4;
}

.punch-circle-btn.is-outside {
  width: 118px;
  height: 118px;
  margin-top: 74px;
  border-width: 6px;
  border-color: #dddddf;
  background: #ffffff;
  color: #9ca3af;
}

.punch-circle-btn.is-outside .punch-circle-btn__title {
  font-size: 16px;
}

.punch-circle-btn.is-outside .punch-circle-btn__time {
  font-size: 20px;
}

.punch-circle-btn.is-submitting::before {
  content: '';
  position: absolute;
  inset: -7px;
  border-radius: 999px;
  background: conic-gradient(from 0deg, #e8c241 0 72deg, rgba(232, 194, 65, 0.18) 72deg 360deg);
  animation: punch-ring-spin 0.9s linear infinite;
  -webkit-mask: radial-gradient(circle, transparent 0 58px, #000 59px);
  mask: radial-gradient(circle, transparent 0 58px, #000 59px);
  z-index: 0;
}

.punch-circle-btn.is-submitting {
  border-color: transparent;
}

.punch-circle-btn > span {
  position: relative;
  z-index: 1;
}

@keyframes punch-ring-spin {
  to {
    transform: rotate(360deg);
  }
}

.punch-circle-btn:disabled {
  opacity: 0.45;
}

.punch-circle-btn__title {
  font-size: 18px;
  font-weight: 700;
}

.punch-circle-btn__time {
  font-size: 21px;
  font-weight: 800;
}

.punch-circle-btn__hint {
  max-width: 92px;
  font-size: 11px;
  font-weight: 700;
  color: #9a6b00;
  line-height: 1.2;
  text-align: center;
}

.punch-rule-line {
  width: 100%;
  margin-top: 14px;
  border: 0;
  background: transparent;
  text-align: center;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  touch-action: manipulation;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
}

.green-dot {
  display: inline-flex;
  width: 7px;
  height: 7px;
  margin: 0 8px 1px;
  border-radius: 999px;
  background: #35b66f;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 9px;
  font-weight: 900;
  line-height: 1;
  vertical-align: middle;
}

.green-dot.is-late,
.green-dot.is-early,
.green-dot.is-pending {
  background: #e8a300;
}

.green-dot.is-danger {
  background: #ef4444;
}

.green-dot.is-abnormal {
  width: 7px;
  height: 7px;
  margin-bottom: 1px;
  background: #e84d5b;
  box-shadow: none;
}

.green-dot.is-done {
  background: #94a3b8;
}

.green-dot.is-hidden {
  display: none;
}

.rule-alert-dot {
  width: 9px;
  height: 9px;
  border-radius: 999px;
  background: #e84d5b;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 7px;
  font-weight: 900;
  line-height: 1;
  transform: translateY(-1px);
}

.chevron {
  margin-left: 2px;
  color: #9ca3af;
}

.abnormal-entry-card {
  width: calc(100% - 24px);
  min-height: 46px;
  margin: 10px 12px 0;
  padding: 0 13px;
  border: 0;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.04);
  display: flex;
  align-items: center;
  gap: 8px;
  color: #1f2937;
  text-align: left;
}

.abnormal-entry-card__left {
  flex: 1;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.abnormal-entry-card__left strong {
  font-size: 14px;
  font-weight: 700;
}

.abnormal-entry-card__icon,
.inline-warning-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border-radius: 999px;
  background: #e84d5b;
  color: #fff;
  font-size: 11px;
  font-style: normal;
  font-weight: 900;
  line-height: 1;
}

.abnormal-entry-card__icon {
  width: auto;
  height: auto;
  border-radius: 0;
  background: transparent;
  color: #111827;
  font-size: 18px;
  font-weight: 600;
}

.abnormal-entry-card__meta {
  color: #0f172a;
  font-size: 14px;
  font-weight: 700;
}

.abnormal-entry-card__action {
  padding: 3px 7px;
  border-radius: 6px;
  background: #edf5ff;
  color: #2f7df6;
  font-size: 12px;
  font-weight: 800;
}

.sheet-value.is-abnormal-text {
  color: #e84d5b;
  font-weight: 800;
}

.inline-warning-mark {
  width: 14px;
  height: 14px;
  margin-right: 5px;
  font-size: 10px;
}

.inline-ok-mark {
  margin-right: 5px;
}

.attendance-quick-nav {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  bottom: 0;
  z-index: 30;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  width: min(100%, var(--mobile-window-width));
  min-height: var(--mobile-bottom-nav-total-height);
  padding-bottom: env(safe-area-inset-bottom);
  border-top: 1px solid var(--mobile-bottom-nav-border);
  border-left: 1px solid var(--mobile-bottom-nav-side-border);
  border-right: 1px solid var(--mobile-bottom-nav-side-border);
  background: var(--mobile-bottom-nav-background);
  backdrop-filter: blur(18px);
}

.quick-nav-item {
  min-height: var(--mobile-bottom-nav-height);
  border: 0;
  background: transparent;
  color: var(--mobile-bottom-nav-color);
  font-size: var(--mobile-bottom-nav-label-size);
  font-weight: var(--mobile-bottom-nav-font-weight);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.quick-nav-item.is-active {
  color: var(--mobile-bottom-nav-active-color);
}

.quick-nav-icon {
  font-size: var(--mobile-bottom-nav-icon-size);
  line-height: 1;
}

.outside-form-list {
  background: #fff;
  border-top: 1px solid #f0f2f5;
}

.outside-form-row {
  min-height: 45px;
  display: grid;
  grid-template-columns: 74px 1fr 24px;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 0 12px;
  border: 0;
  border-bottom: 1px solid #f1f3f6;
  background: #fff;
  text-align: left;
}

.outside-photo-input {
  display: none;
}

.outside-form-label {
  color: #1f2937;
  font-size: 14px;
  font-weight: 600;
}

.outside-placeholder {
  color: #a0a7b5;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.outside-photo-thumb {
  width: 28px;
  height: 28px;
  border-radius: 4px;
  object-fit: cover;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.3);
}

.outside-row-icon {
  color: #8d96a8;
  font-size: 17px;
  text-align: right;
}

.outside-inline-input {
  --padding-start: 0;
  --padding-end: 0;
  --placeholder-color: #9ca3af;
  --placeholder-opacity: 1;
  color: #334155;
  font-size: 13px;
}

.outside-approval-card {
  margin: 14px -10px -18px;
  width: calc(100% + 20px);
  min-height: 70px;
  border: 0;
  border-top: 10px solid #f1f4f8;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  text-align: left;
}

.outside-approval-card strong {
  display: block;
  color: #111827;
  font-size: 14px;
  line-height: 1.5;
}

.outside-approval-card small {
  display: block;
  color: #8b95a7;
  font-size: 11px;
  line-height: 1.4;
}

.outside-approval-card em {
  flex: 0 0 auto;
  min-width: 68px;
  height: 28px;
  border-radius: 3px;
  background: #f4cd46;
  color: #8a6300;
  font-size: 12px;
  font-style: normal;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.mobile-picker-mask {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: flex;
  justify-content: center;
  align-items: flex-end;
  background: rgba(15, 23, 42, 0.18);
}

.address-picker-panel {
  width: min(100%, 430px);
  height: 100%;
  background: #fff;
  display: flex;
  flex-direction: column;
}

.address-picker-map {
  position: relative;
  height: 48%;
  min-height: 280px;
  background: #eef2f7;
  overflow: hidden;
}

.address-picker-map__inner {
  width: 100%;
  height: 100%;
}

.address-picker-confirm {
  position: absolute;
  top: calc(16px + env(safe-area-inset-top));
  right: 18px;
  border: 0;
  border-radius: 4px;
  padding: 9px 18px;
  background: #2f7df6;
  color: #fff;
  font-size: 15px;
  font-weight: 700;
}

.address-picker-sheet {
  flex: 1;
  margin-top: -8px;
  border-radius: 14px 14px 0 0;
  background: #fff;
  padding: 8px 14px calc(18px + env(safe-area-inset-bottom));
  overflow: auto;
  box-shadow: 0 -8px 22px rgba(15, 23, 42, 0.08);
}

.address-picker-handle {
  width: 44px;
  height: 4px;
  margin: 0 auto 14px;
  border-radius: 999px;
  background: #d7dce5;
}

.address-search-box {
  height: 42px;
  border-radius: 6px;
  background: #f4f6f9;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  color: #9ca3af;
}

.address-search-box input {
  flex: 1;
  border: 0;
  outline: none;
  background: transparent;
  font-size: 15px;
  color: #111827;
}

.address-picker-tip {
  margin: 14px 0 10px;
  color: #9ca3af;
  font-size: 13px;
}

.address-suggestion-row {
  position: relative;
  width: 100%;
  min-height: 68px;
  border: 0;
  border-bottom: 1px solid #f0f2f5;
  background: #fff;
  text-align: left;
  padding: 10px 34px 10px 0;
}

.address-suggestion-row strong {
  display: block;
  color: #111827;
  font-size: 16px;
  font-weight: 700;
}

.address-suggestion-row span {
  display: block;
  margin-top: 5px;
  color: #8b95a7;
  font-size: 13px;
}

.address-suggestion-row em {
  position: absolute;
  right: 4px;
  top: 22px;
  color: #2f7df6;
  font-style: normal;
  font-size: 21px;
}

.photo-preview-panel {
  width: min(92%, var(--mobile-window-width));
  margin-bottom: 32px;
  border-radius: 18px;
  background: #fff;
  padding: 12px;
}

.photo-preview-panel img {
  width: 100%;
  max-height: 70vh;
  border-radius: 12px;
  object-fit: contain;
  background: #0f172a;
}

.photo-preview-panel button {
  width: 100%;
  height: 42px;
  margin-top: 10px;
  border: 0;
  border-radius: 10px;
  background: #2f7df6;
  color: #fff;
  font-weight: 700;
}

.early-leave-panel {
  width: min(82vw, 260px);
  margin: auto;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
  overflow: hidden;
  text-align: center;
  color: #1f2937;
}

.early-leave-icon {
  width: 36px;
  height: 36px;
  margin: 20px auto 10px;
  border-radius: 999px;
  background: linear-gradient(135deg, #ee8b61, #e36f45);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 800;
}

.early-leave-panel h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 800;
}

.early-leave-panel strong {
  display: block;
  margin-top: 8px;
  color: #d66d4a;
  font-size: 13px;
}

.early-leave-panel p {
  min-height: 34px;
  margin: 8px 24px 14px;
  color: #8a94a6;
  font-size: 12px;
  line-height: 1.45;
}

.early-leave-panel footer {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  border-top: 1px solid #edf0f5;
}

.early-leave-panel footer button {
  height: 45px;
  border: 0;
  background: #fff;
  color: #4b80c9;
  font-size: 14px;
  font-weight: 700;
}

.early-leave-panel footer button + button {
  border-left: 1px solid #edf0f5;
  color: #2f72c9;
}

.range-confirm-panel {
  width: min(72vw, 250px);
  margin: auto;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.24);
  overflow: hidden;
  text-align: center;
  color: #111827;
}

.range-confirm-pin {
  position: relative;
  width: 46px;
  height: 54px;
  margin: 18px auto 4px;
}

.range-confirm-pin::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 2px;
  width: 31px;
  height: 31px;
  border-radius: 50% 50% 50% 0;
  background: #f39a6d;
  transform: rotate(-45deg);
}

.range-confirm-pin span {
  position: absolute;
  left: 15px;
  top: 7px;
  width: 17px;
  height: 17px;
  border-radius: 999px;
  background: #fff;
  color: #f39a6d;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 900;
  line-height: 1;
}

.range-confirm-panel h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 800;
}

.range-confirm-panel p {
  margin: 8px 0 0;
  color: #f26f5b;
  font-size: 12px;
  font-weight: 600;
}

.range-confirm-proof {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 50px;
  padding: 10px 16px 12px;
  color: #9aa3b3;
  font-size: 12px;
  line-height: 1.35;
}

.range-confirm-check {
  width: 15px;
  height: 15px;
  border-radius: 999px;
  background: #3b82f6;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  font-size: 10px;
  font-weight: 900;
}

.range-confirm-panel footer {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  border-top: 1px solid #edf0f5;
}

.range-confirm-panel footer button {
  height: 44px;
  border: 0;
  background: #fff;
  color: #4b80c9;
  font-size: 14px;
  font-weight: 700;
}

.range-confirm-panel footer button + button {
  border-left: 1px solid #edf0f5;
  color: #2f72c9;
}

.record-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
}

.today-record-card {
  margin-top: 10px;
  padding: 13px 14px 14px;
  border-radius: 10px;
  box-shadow: none;
}

.today-record-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid #eef1f5;
  border-bottom: 1px solid #f4f6f9;
}

.today-record-cell {
  min-width: 0;
  min-height: 52px;
  padding: 9px 4px 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
}

.today-record-cell + .today-record-cell {
  border-left: 1px solid #f0f3f7;
}

.today-record-label {
  color: #8b95a7;
  font-size: 11px;
  line-height: 1;
}

.today-record-value {
  max-width: 100%;
  color: #111827;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.approval-template-page {
  --approval-line: #dce3ec;
  --approval-line-strong: #d5dde8;
  --approval-blue: #25a8f2;
  --approval-yellow: #ffb72f;
  --approval-teal: #40cfc3;
  color: #23262f;
}

.approval-template-search {
  height: 44px;
  margin: 0 10px 12px;
  border-radius: 4px;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  color: #8f98a6;
}

.approval-template-search span {
  flex: 0 0 auto;
  font-size: 22px;
}

.approval-template-search input {
  width: 140px;
  min-width: 0;
  border: 0;
  outline: 0;
  padding: 0;
  background: transparent;
  color: #111827;
  font: inherit;
  font-size: 17px;
  line-height: 24px;
}

.approval-template-search input::placeholder {
  color: #9aa2af;
}

.approval-template-sections {
  display: grid;
  gap: 12px;
  padding: 0 10px;
}

.approval-template-section {
  overflow: hidden;
  border-radius: 5px;
  background: #ffffff;
  border: 1px solid var(--approval-line-strong);
}

.approval-template-section h2 {
  height: 54px;
  margin: 0;
  padding: 0 13px;
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--approval-line);
  color: #919aa7;
  font-size: 17px;
  font-weight: 400;
  letter-spacing: 0;
}

.approval-template-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  background: var(--approval-line);
}

.approval-template-item {
  min-height: 130px;
  border: 0;
  margin: 0 -1px -1px 0;
  border-right: 1px solid var(--approval-line);
  border-bottom: 1px solid var(--approval-line);
  background: #ffffff;
  color: #111827;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 12px;
  text-align: center;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.32;
  white-space: pre-line;
}

.approval-template-item:active {
  background: #f7faff;
}

.approval-template-item:nth-child(3n) {
  margin-right: 0;
}

.approval-template-item--placeholder {
  pointer-events: none;
}

.approval-template-icon {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  font-size: 29px;
}

.approval-template-icon--yellow {
  color: var(--approval-yellow);
}

.approval-template-icon--teal {
  color: var(--approval-teal);
}

.approval-template-icon--blue {
  color: var(--approval-blue);
}

.approval-template-name {
  width: min(98px, calc(100% - 18px));
  margin: 0 auto;
  display: block;
  overflow-wrap: break-word;
  text-align: center;
}

.approval-template-empty {
  margin: 0 10px;
  min-height: 96px;
  border-radius: 4px;
  background: #ffffff;
  color: #8c929c;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  text-align: center;
  font-size: 14px;
}

.approval-template-empty strong {
  color: #4b5563;
  font-size: 15px;
}

.apply-record-row {
  width: calc(100% - 20px);
  min-height: 46px;
  margin: 10px auto 0;
  border: 0;
  border-radius: 8px;
  background: #fff;
  color: #111827;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  font-size: 14px;
  font-weight: 700;
}

.apply-record-left {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.apply-record-icon {
  color: #111827;
  font-size: 18px;
  font-weight: 400;
}

.apply-record-count {
  color: #9ca3af;
  font-weight: 500;
}

.stats-mobile-head {
  padding: 8px 0 0;
  color: #111827;
}

.stats-year-line {
  padding: 0 0 8px;
  font-size: 14px;
  font-weight: 800;
}

.stats-head-row {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 34px;
}

.stats-period-title {
  flex: 1;
  text-align: center;
  font-size: 15px;
  font-weight: 800;
}

.stats-arrow {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 999px;
  background: #fff;
  color: #7b8797;
  font-size: 20px;
}

.stats-top-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.stats-month-text {
  font-size: 20px;
  font-weight: 700;
  color: #111827;
}

.stats-toggle-row {
  margin-left: auto;
  display: flex;
  gap: 0;
  border-radius: 7px;
  background: #e9edf3;
  padding: 2px;
}

.stats-range-pill {
  min-width: 30px;
  min-height: 26px;
  border-radius: 6px;
  border: 0;
  background: transparent;
  color: #6b7280;
  font-size: 12px;
  font-weight: 700;
}

.stats-range-pill.is-active {
  background: #fff;
  color: #111827;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

.stats-week-strip {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  align-items: center;
  gap: 4px;
  padding: 8px 0 12px;
}

.stats-day-cell {
  min-height: 54px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: #111827;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.stats-day-cell span {
  color: #8b95a7;
  font-size: 11px;
}

.stats-day-cell strong {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
}

.stats-day-cell.is-active strong {
  background: #1c8bf3;
  color: #fff;
}

.stats-export-row,
.stats-total-row {
  min-height: 46px;
  border-radius: 8px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
}

.stats-export-row button,
.stats-help-line button {
  border: 0;
  background: transparent;
  color: #111827;
  font-size: 14px;
  font-weight: 700;
}

.stats-export-row button:last-child {
  color: #64748b;
  font-size: 13px;
  font-weight: 500;
}

.stats-card-title-btn {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
}

.stats-day-card,
.stats-summary-card,
.stats-list-card {
  padding: 14px 14px 16px;
  border-radius: 8px;
  box-shadow: none;
}

.stats-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.stats-rule-title-btn {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
}

.stats-card-head strong {
  display: block;
  color: #111827;
  font-size: 14px;
  line-height: 1.5;
}

.stats-card-head span {
  display: block;
  color: #9ca3af;
  font-size: 11px;
  line-height: 1.5;
}

.stats-card-head em {
  color: #aab2c0;
  font-size: 18px;
  font-style: normal;
}

.plain-chevron-btn {
  border: 0;
  background: transparent;
  color: #aab2c0;
  font-size: 18px;
  line-height: 1;
}

.day-time-line {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
  margin-top: 18px;
  padding-left: 46px;
}

.day-time-row {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr) auto;
  grid-template-rows: auto auto;
  column-gap: 10px;
  min-height: 48px;
  color: #111827;
}

.day-time-row strong {
  grid-row: span 2;
  align-self: start;
  font-size: 20px;
  line-height: 1;
}

.day-time-row strong.is-muted-clock {
  color: #c4cbd6;
  font-weight: 600;
}

.day-time-row span {
  font-size: 14px;
  font-weight: 700;
}

.day-time-row small {
  color: #9ca3af;
  font-size: 12px;
}

.day-time-row .is-abnormal-clock {
  color: #e84d5b;
}

.inline-process-btn {
  grid-column: 3;
  grid-row: 2;
  align-self: start;
  border: 0;
  background: transparent;
  color: #2f7df6;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.day-time-line i {
  width: 1px;
  height: 34px;
  margin-left: 3px;
  background: #dde3eb;
}

.stats-help-line {
  margin: 8px 0 0;
  color: #9ca3af;
  font-size: 12px;
}

.stats-help-line button {
  padding: 0;
  color: #2f7df6;
  font-size: 12px;
}

.stats-gauge {
  --abnormal: 0%;
  position: relative;
  width: 148px;
  height: 76px;
  margin: 12px auto 18px;
  border-radius: 148px 148px 0 0;
  background:
    radial-gradient(circle at 50% 100%, #fff 0 55px, transparent 56px),
    conic-gradient(from 270deg at 50% 100%, #5b98f2 0 calc(50% - var(--abnormal)), #f05264 calc(50% - var(--abnormal)) 50%, transparent 50% 100%);
}

.gauge-legend {
  position: absolute;
  left: 50%;
  border: 0;
  padding: 0;
  background: transparent;
  transform: translateX(-50%);
  color: #64748b;
  font-size: 10px;
  white-space: nowrap;
}

.gauge-legend.normal {
  top: 42px;
}

.gauge-legend.abnormal {
  top: 58px;
}

.gauge-legend.normal::before,
.gauge-legend.abnormal::before {
  content: '';
  display: inline-block;
  width: 4px;
  height: 4px;
  margin-right: 4px;
  border-radius: 999px;
  vertical-align: 2px;
  background: #5b98f2;
}

.gauge-legend.abnormal::before {
  background: #f05264;
}

.stats-metric-row,
.stats-leave-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 4px;
  color: #9ca3af;
  text-align: center;
}

.stats-metric-row button,
.stats-leave-row button {
  min-width: 0;
  min-height: 42px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #9ca3af;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  text-align: center;
  touch-action: manipulation;
}

.stats-metric-row strong {
  min-height: 18px;
  color: #ef4444;
  font-size: 14px;
  font-weight: 700;
}

.stats-metric-row span {
  font-size: 11px;
}

.stats-leave-row button {
  min-height: 32px;
  font-size: 11px;
}

.stats-metric-row button:active,
.stats-leave-row button:active,
.stats-card-title-btn:active,
.gauge-legend:active {
  background: #f5f8fc;
}

.stats-summary-divider {
  height: 1px;
  margin: 14px 0 12px;
  background: #edf1f6;
}

.stats-total-row {
  width: 100%;
  border: 0;
  color: #111827;
  cursor: pointer;
  text-align: left;
  touch-action: manipulation;
}

.stats-total-row strong,
.stats-total-row span {
  font-size: 14px;
  font-weight: 800;
}

.stats-record-list-card,
.punch-detail-card,
.correction-form-card {
  padding: 14px;
  border-radius: 8px;
  box-shadow: none;
}

.punch-detail-card,
.correction-form-card {
  margin: 0 -10px;
  padding: 0 14px;
  border-radius: 0;
  min-height: calc(100vh - 72px);
}

.calibration-filter-trigger {
  width: 100%;
  min-height: 36px;
  border: 0;
  border-bottom: 1px solid #eef1f5;
  background: #fff;
  color: #2f7df6;
  font-size: 13px;
  font-weight: 700;
  text-align: center;
}

.monthly-detail-list {
  display: flex;
  flex-direction: column;
}

.monthly-detail-row {
  min-height: 46px;
  border: 0;
  border-bottom: 1px solid #f1f3f6;
  background: #fff;
  color: #111827;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 14px;
  font-weight: 600;
  text-align: left;
}

.monthly-detail-row-main {
  flex: 1;
  min-height: 46px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
}

.monthly-detail-row.is-abnormal span {
  color: #e84d5b;
}

.monthly-detail-row.is-abnormal .monthly-detail-row-main {
  color: #e84d5b;
}

.monthly-detail-row em,
.monthly-detail-action-btn {
  flex: 0 0 auto;
  min-width: 42px;
  border: 0;
  border-radius: 4px;
  padding: 4px 8px;
  background: #edf5ff;
  color: #2f7df6;
  font-size: 12px;
  font-style: normal;
  font-weight: 700;
  text-align: center;
}

.leave-detail-row {
  width: 100%;
  padding: 0;
}

.leave-detail-row span {
  flex: 1;
  min-width: 0;
}

.leave-detail-row small {
  flex: 0 0 auto;
  color: #8b95a7;
  font-size: 12px;
  font-weight: 500;
}

.process-btn {
  flex: 0 0 auto;
  min-width: 45px;
  height: 30px;
  border: 0;
  border-radius: 2px;
  background: #2f7df6;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.rule-profile-card {
  margin: 10px 0 8px;
  padding: 14px 14px;
  background: #fff;
  display: flex;
  align-items: center;
  gap: 12px;
}

.rule-avatar {
  width: 42px;
  height: 42px;
  border-radius: 3px;
  background: #dce8f6;
  color: #1f2937;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 800;
}

.rule-profile-card strong {
  display: block;
  color: #111827;
  font-size: 16px;
  font-weight: 700;
}

.rule-profile-card span {
  display: block;
  margin-top: 4px;
  color: #8b95a7;
  font-size: 13px;
}

.rule-section-card {
  margin-top: 8px;
  background: #fff;
}

.rule-section-head {
  width: 100%;
  min-height: 48px;
  padding: 0 14px;
  border: 0;
  border-bottom: 1px solid #eef1f5;
  background: #fff;
  color: #111827;
  display: flex;
  align-items: center;
  justify-content: space-between;
  text-align: left;
}

.rule-section-head strong {
  font-size: 16px;
  font-weight: 800;
}

.rule-section-head span {
  color: #a0a7b5;
  font-size: 18px;
}

.rule-section-body {
  padding: 14px;
  color: #6b7280;
  font-size: 14px;
  line-height: 1.65;
}

.rule-section-body p {
  margin: 0 0 8px;
}

.rule-label {
  color: #111827;
  font-size: 15px;
  font-weight: 700;
}

.muted-rule-text {
  color: #8b95a7;
}

.location-tag {
  display: inline-flex;
  padding: 3px 7px;
  border-radius: 2px;
  background: #eef0f3;
  color: #6b7280;
  font-style: normal;
}

.month-detail-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  height: 46px;
  margin: 0 -10px;
  background: #fff;
  border-bottom: 1px solid #e8edf4;
}

.month-detail-tabs button {
  position: relative;
  border: 0;
  background: transparent;
  color: #6b7280;
  font-size: 14px;
  font-weight: 700;
}

.month-detail-tabs button.is-active {
  color: #111827;
}

.month-detail-tabs button.is-active::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: 0;
  width: 40px;
  height: 3px;
  border-radius: 999px;
  background: #2f7df6;
  transform: translateX(-50%);
}

.month-detail-panel {
  margin: 0 -10px;
  min-height: calc(100vh - 210px);
  background: #fff;
}

.month-filter {
  min-height: 42px;
  border-bottom: 1px solid #eef1f5;
}

.wecom-list {
  padding: 0 14px;
}

.empty-month-panel {
  padding: 56px 0;
  color: #9ca3af;
  font-size: 14px;
  text-align: center;
}

.month-filter-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  height: 42px;
  border-bottom: 1px solid #eef1f5;
}

.month-filter-row button {
  border: 0;
  background: #fff;
  color: #6b7280;
  font-size: 13px;
}

.overtime-table-head,
.overtime-table-row {
  display: grid;
  grid-template-columns: 1.15fr 1fr 1fr 1.1fr;
  min-height: 44px;
  align-items: center;
  padding: 0 14px;
  border-bottom: 1px solid #eef1f5;
  color: #111827;
  font-size: 14px;
}

.overtime-table-head {
  min-height: 36px;
  background: #eef2f7;
  color: #6b7280;
  font-size: 12px;
}

.overtime-table-head--monthly,
.overtime-table-row--monthly {
  grid-template-columns: 1.25fr 1fr 1fr;
}

.overtime-table-head--daily,
.overtime-table-row--daily {
  grid-template-columns: 1fr 1fr 1.05fr;
}

.overtime-table-row--daily {
  min-height: 56px;
}

.overtime-table-row--daily > span {
  min-width: 0;
}

.overtime-table-row--daily strong,
.overtime-table-row--daily small {
  display: block;
}

.overtime-table-row--daily strong {
  color: #111827;
  font-size: 14px;
  line-height: 1.35;
}

.overtime-table-row--daily small {
  margin-top: 3px;
  color: #8b95a7;
  font-size: 11px;
  line-height: 1.25;
}

.export-report-card {
  margin-top: 18px;
  padding: 0 14px;
  border-radius: 8px;
  background: #fff;
}

.export-option-row {
  width: 100%;
  min-height: 54px;
  border: 0;
  border-bottom: 1px solid #edf1f6;
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  color: #111827;
  font-size: 15px;
  text-align: left;
}

.export-option-row:last-child {
  border-bottom: 0;
}

.export-option-row span {
  font-weight: 700;
}

.export-option-row strong {
  color: #6b7280;
  font-size: 14px;
  font-weight: 500;
}

.export-submit-btn {
  width: calc(100% - 52px);
  min-height: 44px;
  margin: 28px 26px 0;
  border: 0;
  border-radius: 4px;
  background: #1f6bd5;
  color: #fff;
  font-size: 15px;
  font-weight: 800;
}

.export-submit-btn:disabled {
  opacity: 0.62;
}

.export-help-text {
  margin: 24px 0 0;
  color: #8b95a7;
  font-size: 13px;
  text-align: center;
}

.month-confirm-btn {
  position: fixed;
  left: 50%;
  bottom: 22px;
  z-index: 35;
  width: min(calc(100% - 40px), 350px);
  min-height: 44px;
  border: 0;
  border-radius: 3px;
  background: #f2f4f7;
  color: #2f72c9;
  font-size: 15px;
  font-weight: 800;
  transform: translateX(-50%);
}

.stats-empty-state {
  padding: 18px 0 6px;
  color: #9ca3af;
  font-size: 13px;
  text-align: center;
}

.punch-detail-status {
  min-height: 166px;
  border-bottom: 1px solid #eef1f5;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #1f8f57;
}

.punch-detail-status.is-abnormal {
  color: #e84d5b;
}

.punch-detail-status span {
  width: 48px;
  height: 48px;
  border-radius: 999px;
  background: currentColor;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: 900;
}

.punch-detail-status strong {
  color: #111827;
  font-size: 17px;
  font-weight: 800;
}

.punch-detail-status small {
  color: currentColor;
  font-size: 13px;
  font-weight: 700;
}

.detail-row-list {
  display: flex;
  flex-direction: column;
}

.detail-row {
  min-height: 50px;
  padding: 11px 0;
  border-bottom: 1px solid #f1f3f6;
  display: grid;
  grid-template-columns: 74px 1fr;
  gap: 12px;
  align-items: center;
}

.detail-row span {
  color: #6b7280;
  font-size: 13px;
}

.detail-row strong {
  color: #111827;
  font-size: 14px;
  font-weight: 700;
  text-align: left;
}

.detail-row strong.is-danger-text {
  color: #e84d5b;
}

.detail-row small {
  grid-column: 2;
  color: #9ca3af;
  font-size: 12px;
}

.detail-row img {
  grid-column: 2;
  width: 100%;
  height: 92px;
  margin-top: 8px;
  border-radius: 4px;
  object-fit: cover;
}

.detail-action-list {
  display: flex;
  flex-direction: column;
  margin: 6px -14px -14px;
  background: #fff;
}

.detail-action-list button {
  min-height: 44px;
  border: 0;
  border-top: 1px solid #eef1f5;
  background: #fff;
  color: #2f7df6;
  font-size: 14px;
  font-weight: 700;
  text-align: left;
  padding: 0 14px;
}

.punch-detail-rule {
  margin-top: 18px;
}

.punch-detail-actions {
  margin-top: 10px;
  border-top: 1px solid #edf0f5;
  background: #ffffff;
  display: grid;
}

.punch-detail-actions button {
  min-height: 48px;
  border: 0;
  border-bottom: 1px solid #edf0f5;
  background: #ffffff;
  color: #2f7df6;
  font-size: 15px;
  font-weight: 700;
  text-align: left;
}

.punch-detail-actions button:last-child {
  border-bottom: 0;
}

.clock-out-result-card {
  margin: 0 -10px 10px;
  padding: 10px 0 0;
  border-radius: 0;
  min-height: calc(100vh - 202px);
  border: 0;
  background: transparent;
  box-shadow: none;
}

.clock-out-result-tabs {
  margin: 0 0 10px;
  height: 52px;
  border-radius: 8px;
}

.clock-out-result-content {
  min-height: calc(100vh - 292px);
  padding: 100px 28px 18px;
  border-radius: 8px;
  background: #fff;
  display: flex;
  flex-direction: column;
  align-items: stretch;
}

.clock-out-result-icon {
  position: relative;
  width: 66px;
  height: 66px;
  margin: 0 auto 26px;
  border-radius: 999px;
  background: #ff514f;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0;
  font-weight: 900;
}

.clock-out-result-icon.is-normal {
  background: #2fbb73;
}

.clock-out-result-icon::before,
.clock-out-result-icon::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  height: 5px;
  border-radius: 999px;
  background: #fff;
  transform-origin: left center;
}

.clock-out-result-icon::before {
  width: 30px;
  transform: translate(-2px, -1px) rotate(-30deg);
}

.clock-out-result-icon::after {
  width: 27px;
  transform: translate(-2px, -1px) rotate(210deg);
}

.clock-out-result-icon span,
.clock-out-result-icon i {
  display: none;
}

.clock-out-result-content h2 {
  margin: 0;
  color: #111827;
  font-size: 20px;
  font-weight: 800;
  text-align: center;
}

.clock-out-result-content p {
  margin: 10px 0 78px;
  color: #9ca3af;
  font-size: 14px;
  font-weight: 700;
  text-align: center;
}

.clock-out-result-rows {
  border-top: 1px solid #f0f2f5;
  border-bottom: 1px solid #f0f2f5;
}

.clock-out-result-rows div {
  min-height: 34px;
  display: flex;
  align-items: center;
  gap: 4px;
  color: #111827;
  font-size: 15px;
  font-weight: 700;
}

.clock-out-result-rows span {
  color: #111827;
  font-weight: 500;
}

.clock-out-result-rows strong {
  color: #111827;
  font-size: 15px;
}

.clock-out-result-rows strong.is-danger-text {
  color: #e84d5b;
}

.clock-out-result-actions {
  min-height: 46px;
  display: flex;
  align-items: center;
  gap: 16px;
  border-bottom: 1px solid #f0f2f5;
}

.clock-out-result-actions button {
  border: 0;
  padding: 0;
  background: transparent;
  color: #2f7df6;
  font-size: 13px;
  font-weight: 700;
}

.clock-out-result-rule {
  margin-top: auto;
  padding-bottom: 6px;
}

.correction-form-title {
  display: none;
}

.correction-form-title strong {
  display: block;
  color: #111827;
  font-size: 17px;
  font-weight: 800;
}

.correction-form-title span {
  display: block;
  margin-top: 5px;
  color: #8b95a7;
  font-size: 12px;
  line-height: 1.5;
}

.correction-field {
  min-height: 64px;
  border-bottom: 1px solid #f1f3f6;
  display: grid;
  grid-template-columns: 112px 1fr;
  gap: 12px;
  align-items: center;
}

.correction-field span {
  color: #111827;
  font-size: 16px;
  font-weight: 500;
}

.correction-field i {
  color: #e84d5b;
  font-style: normal;
}

.correction-field input,
.correction-field select,
.correction-field textarea {
  width: 100%;
  border: 0;
  outline: 0;
  background: #fff;
  color: #111827;
  font-size: 16px;
  text-align: right;
}

.correction-field--textarea {
  grid-template-columns: 1fr;
  align-items: start;
  padding: 16px 0 18px;
}

.correction-field--textarea textarea {
  min-height: 78px;
  padding: 8px 0;
  resize: none;
  text-align: left;
}

.approval-flow-preview {
  margin: 10px -14px 0;
  padding: 14px;
  border-radius: 0;
  background: #fff;
  border-top: 10px solid #eef2f7;
}

.approval-flow-preview strong {
  display: block;
  color: #111827;
  font-size: 14px;
}

.approval-flow-preview p {
  margin: 5px 0 12px;
  color: #8b95a7;
  font-size: 12px;
  line-height: 1.5;
}

.flow-dot-row {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #64748b;
  font-size: 13px;
}

.flow-dot-row span {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: #cbd5e1;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 800;
}

.flow-dot-row em {
  font-style: normal;
}

.correction-submit-btn {
  position: fixed;
  left: 50%;
  bottom: 22px;
  z-index: 35;
  width: min(calc(100% - 40px), 350px);
  min-height: 44px;
  margin-top: 0;
  border: 0;
  border-radius: 3px;
  background: #2f7df6;
  color: #fff;
  font-size: 15px;
  font-weight: 800;
  transform: translateX(-50%);
}

.correction-submit-btn:disabled {
  opacity: 0.5;
}

.status-filter-panel {
  width: min(100%, var(--mobile-window-width));
  max-height: 70vh;
  overflow: auto;
  border-radius: 10px 10px 0 0;
  background: #fff;
}

.status-filter-panel h2 {
  margin: 0;
  min-height: 44px;
  border-bottom: 1px solid #eef1f5;
  color: #64748b;
  font-size: 13px;
  font-weight: 600;
  line-height: 44px;
  text-align: center;
}

.status-filter-panel > button {
  width: 100%;
  min-height: 44px;
  border: 0;
  border-bottom: 1px solid #f1f3f6;
  background: #fff;
  color: #111827;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  font-size: 14px;
}

.status-filter-panel > button.is-selected {
  color: #2f7df6;
  font-weight: 800;
}

.status-filter-panel > button em {
  font-style: normal;
}

.status-filter-panel footer {
  border-top: 6px solid #f1f4f8;
}

.status-filter-panel footer button {
  width: 100%;
  min-height: 48px;
  border: 0;
  background: #fff;
  color: #111827;
  font-size: 14px;
  font-weight: 700;
}

.application-sheet-panel {
  width: min(100%, var(--mobile-window-width));
  border-radius: 10px 10px 0 0;
  background: #eef2f7;
  overflow: hidden;
}

.application-sheet-panel header {
  min-height: 48px;
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #111827;
}

.application-sheet-panel header strong {
  font-size: 15px;
}

.application-sheet-panel header button {
  border: 0;
  background: transparent;
  color: #8b95a7;
  font-size: 20px;
}

.application-sheet-panel > button {
  width: calc(100% - 20px);
  min-height: 50px;
  margin: 0 10px;
  border: 0;
  border-bottom: 1px solid #eef1f5;
  background: #fff;
  color: #111827;
  display: grid;
  grid-template-columns: 34px 1fr 18px;
  align-items: center;
  gap: 8px;
  text-align: left;
  font-size: 14px;
}

.application-sheet-panel > button:first-of-type {
  border-radius: 8px 8px 0 0;
}

.application-sheet-panel > button:last-of-type {
  margin-bottom: 12px;
  border-radius: 0 0 8px 8px;
  border-bottom: 0;
}

.sheet-action-icon {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}

.sheet-action-icon.is-blue {
  background: #22aeea;
}

.sheet-action-icon.is-cyan {
  background: #26bdd8;
}

.sheet-action-icon.is-yellow {
  background: #f4c430;
}

.sheet-action-icon.is-sky {
  background: #55b2ef;
}

.application-sheet-panel em {
  color: #a0a7b5;
  font-style: normal;
  text-align: right;
}

.device-card {
  padding: 18px 16px;
  border-radius: 8px;
}

.device-card h2 {
  margin: 0 0 8px;
  color: #111827;
  font-size: 16px;
}

.device-card p {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.6;
}

.stats-action-row {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.record-item {
  border-radius: 10px;
  border: 1px solid #e4ebf7;
  background: #fbfdff;
  padding: 10px 12px;
}

:deep(.outside-form-row .native-input) {
  padding-left: 0;
}
</style>

<style scoped>
.project-punch-context{display:flex;flex-direction:column;gap:6px;padding:14px 16px;margin-bottom:16px;border-radius:10px;background:#edf6f4;color:#17665c}.project-punch-context strong{font-size:16px}.project-punch-context span{font-size:12px;color:#587c75}
</style>
