<template>
  <ion-page>
    <ion-content fullscreen class="approval-template-content">
      <div class="mobile-shell approval-start-page">
        <section v-if="!selectedTemplate" class="approval-template-page" aria-label="发起申请模板">
          <header class="approval-template-header">
            <button class="approval-template-back" type="button" aria-label="返回" @click="goBackFromApprovalStart">
              <ion-icon :icon="chevronBackOutline" />
            </button>
            <h1>发起申请</h1>
          </header>

          <label class="approval-template-search">
            <ion-icon :icon="searchOutline" />
            <input v-model="searchKeyword" type="search" placeholder="搜索模板名称" />
          </label>

          <section v-if="loading" class="approval-template-loading">
            <ion-spinner name="crescent" />
            <span>正在加载可用申请模板</span>
          </section>

          <section v-else class="approval-template-sections">
            <article
              v-for="group in visibleTemplateGroups"
              :key="group.category"
              class="approval-template-section"
            >
              <h2>{{ group.category }}</h2>
              <div class="approval-template-grid">
                <template v-for="slot in templateGridSlots(group.templates)" :key="slot.key">
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
                  @click="openTemplate(slot.template)"
                >
                  <span class="approval-template-icon" :class="`approval-template-icon--${slot.template.color}`">
                    <ion-icon :icon="slot.template.icon" />
                  </span>
                  <span class="approval-template-name">{{ slot.template.name }}</span>
                </button>
                </template>
              </div>
            </article>
            <section v-if="!visibleTemplateGroups.length" class="approval-template-empty">
              <strong>暂时没有匹配模板</strong>
              <span>换个关键词再试试。</span>
            </section>
          </section>
        </section>

        <section v-else class="wecom-approval-page" :aria-label="selectedTemplate.name">
          <header class="wecom-form-header">
            <button class="wecom-form-icon-button" type="button" aria-label="返回" @click="goBackFromSelectedTemplate">
              <ion-icon :icon="chevronBackOutline" />
            </button>
            <h1>{{ selectedTemplate.name }}</h1>
            <button class="wecom-form-icon-button" type="button" aria-label="申请记录" @click="router.push('/app/tabs/approvals?segment=submitted')">
              <ion-icon :icon="timeOutline" />
            </button>
          </header>

          <div class="wecom-form-scroll">
            <section class="wecom-form-notice">{{ templateNoticeText }}</section>

            <section class="wecom-form-section" aria-label="申请信息">
              <template v-for="field in visibleTemplateFields(selectedTemplate)" :key="field.code">
                <div v-if="controlType(field) === 'layout_column'" class="wecom-layout-field" aria-hidden="true">
                  <span></span>
                  <span></span>
                </div>

                <div v-else-if="isLongTextField(field)" class="wecom-textarea-field">
                  <label class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </label>
                  <textarea v-model="formData[field.code]" :placeholder="fieldPlaceholder(field)" />
                </div>

                <div v-else-if="isDateTimeField(field)" class="wecom-field-group">
                  <button
                    type="button"
                    class="wecom-field-row wecom-date-trigger"
                    @click="openDateTimePicker(field, field.label || field.code)"
                  >
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                      {{ field.label || field.code }}
                    </span>
                    <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code] }">
                      {{ dateTimeDisplayValue(formData[field.code], dateTimePresentation(field), fieldPlaceholder(field)) }}
                      <ion-icon :icon="chevronForwardOutline" />
                    </span>
                  </button>
                  <p v-if="showPunchCorrectionMonthlyHint(field)" class="wecom-field-note wecom-punch-monthly-note">
                    本月已补卡{{ punchCorrectionMonthUsedCount }}次
                  </p>
                </div>

                <label v-else-if="isSimpleInputField(field)" class="wecom-field-row">
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <input
                    v-model="formData[field.code]"
                    class="wecom-row-input"
                    :type="ionInputType(field)"
                    :placeholder="fieldPlaceholder(field)"
                  />
                </label>

                <div v-else-if="isLeaveTypeBalanceField(field)" class="wecom-field-group">
                  <button
                    type="button"
                    class="wecom-field-row wecom-leave-type-trigger"
                    @click="openLeaveTypePicker(field)"
                  >
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                      {{ field.label || field.code }}
                    </span>
                    <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code] }">
                      {{ leaveTypeDisplayValue(field) }}
                      <ion-icon :icon="chevronForwardOutline" />
                    </span>
                  </button>
                  <p v-if="showLeaveBalanceLink(field)" class="wecom-field-note">
                    <span v-if="leaveTypeHelpText(field)">{{ leaveTypeHelpText(field) }}</span>
                    <button type="button" @click="openLeaveTypePicker(field)">查看假期余额</button>
                  </p>
                  <p v-else-if="leaveBalanceError" class="wecom-field-note wecom-field-note--danger">{{ leaveBalanceError }}</p>
                </div>

                <label v-else-if="controlType(field) === 'select' || controlType(field) === 'cascade'" class="wecom-field-row">
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <span class="wecom-field-value">
                    <select
                      v-model="formData[field.code]"
                      class="wecom-row-select"
                      :class="{ 'wecom-row-select--selected': formData[field.code] }"
                      :disabled="isPunchCorrectionSlotField(field) && !fieldOptions(field).length"
                      @change="handleSelectFieldChange(field)"
                    >
                      <option value="">{{ fieldPlaceholder(field) }}</option>
                      <option v-for="option in fieldOptions(field)" :key="option" :value="option">
                        {{ option }}
                      </option>
                    </select>
                    <ion-icon :icon="chevronForwardOutline" />
                  </span>
                </label>

                <div v-else-if="controlType(field) === 'radio'" class="wecom-choice-field">
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <div class="wecom-choice-grid">
                    <button
                      v-for="option in fieldOptions(field)"
                      :key="option"
                      type="button"
                      :class="{ selected: formData[field.code] === option }"
                      @click="formData[field.code] = option"
                    >
                      {{ option }}
                    </button>
                  </div>
                </div>

                <div v-else-if="controlType(field) === 'checkbox'" class="wecom-choice-field">
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <div class="wecom-choice-grid">
                    <button
                      v-for="option in fieldOptions(field)"
                      :key="option"
                      type="button"
                      :class="{ selected: arrayFieldValue(field).includes(option) }"
                      @click="toggleArrayValue(field, option)"
                    >
                      {{ option }}
                    </button>
                  </div>
                </div>

                <button
                  v-else-if="controlType(field) === 'company'"
                  type="button"
                  class="wecom-field-row wecom-company-trigger"
                  @click="openCompanyPicker(field)"
                >
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code] }">
                    {{ companyDisplayValue(field) }}
                    <ion-icon :icon="chevronForwardOutline" />
                  </span>
                </button>

                <button
                  v-else-if="controlType(field) === 'member' || controlType(field) === 'department'"
                  type="button"
                  class="wecom-field-row wecom-org-trigger"
                  @click="openOrgPicker(field)"
                >
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code] }">
                    {{ organizationDisplayValue(field) }}
                    <ion-icon :icon="chevronForwardOutline" />
                  </span>
                </button>

                <div v-else-if="controlType(field) === 'date_range'" class="wecom-duration-control">
                  <button
                    type="button"
                    class="wecom-field-row wecom-date-trigger"
                    @click="openDateTimePicker(field, '开始日期', 'start', 'date')"
                  >
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">开始日期</span>
                    <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code].start }">
                      {{ dateTimeDisplayValue(formData[field.code].start, 'date', '请选择日期') }}
                      <ion-icon :icon="chevronForwardOutline" />
                    </span>
                  </button>
                  <button
                    type="button"
                    class="wecom-field-row wecom-date-trigger"
                    @click="openDateTimePicker(field, '结束日期', 'end', 'date')"
                  >
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">结束日期</span>
                    <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code].end }">
                      {{ dateTimeDisplayValue(formData[field.code].end, 'date', '请选择日期') }}
                      <ion-icon :icon="chevronForwardOutline" />
                    </span>
                  </button>
                </div>

                <div v-else-if="controlType(field) === 'duration'" class="wecom-duration-control">
                  <button
                    type="button"
                    class="wecom-field-row wecom-date-trigger"
                    @click="openDateTimePicker(field, '开始时间', 'start', durationPickerPresentation(field))"
                  >
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">开始时间</span>
                    <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code].start }">
                      {{ dateTimeDisplayValue(formData[field.code].start, durationPickerPresentation(field), durationPickerPlaceholder(field)) }}
                      <ion-icon :icon="chevronForwardOutline" />
                    </span>
                  </button>
                  <button
                    type="button"
                    class="wecom-field-row wecom-date-trigger"
                    @click="openDateTimePicker(field, '结束时间', 'end', durationPickerPresentation(field))"
                  >
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">结束时间</span>
                    <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code].end }">
                      {{ dateTimeDisplayValue(formData[field.code].end, durationPickerPresentation(field), durationPickerPlaceholder(field)) }}
                      <ion-icon :icon="chevronForwardOutline" />
                    </span>
                  </button>
                  <label class="wecom-field-row">
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                      {{ field.label || '时长' }}
                    </span>
                    <span
                      class="wecom-duration-value"
                      :class="{ 'wecom-field-value--placeholder': !durationDisplayValue(field) }"
                    >
                      <input
                        readonly
                        aria-label="自动计算时长"
                        :value="durationDisplayValue(field) || '0'"
                      />
                      <span>{{ durationUnitLabel(field) }}</span>
                    </span>
                  </label>
                  <p class="wecom-field-note wecom-field-note--duration">
                    <span>{{ durationAutoHint(field) }}</span>
                    <button type="button" @click="showDurationDetail(field)">查看时长明细</button>
                  </p>
                </div>

                <button
                  v-else-if="controlType(field) === 'location'"
                  type="button"
                  class="wecom-field-row wecom-location-trigger"
                  @click="setLocationValue(field)"
                >
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code] }">
                    {{ formData[field.code] || fieldPlaceholder(field) }}
                    <ion-icon :icon="chevronForwardOutline" />
                  </span>
                </button>

                <div
                  v-else-if="['attachment', 'image', 'signature'].includes(controlType(field))"
                  :class="attachmentControlClass(field)"
                >
                  <button
                    type="button"
                    class="wecom-attachment-upload-button"
                    :disabled="isAttachmentUploading(field)"
                    @click="pickAttachment(field)"
                  >
                    <ion-icon :icon="addCircleOutline" />
                    <span>{{ isAttachmentUploading(field) ? '上传中' : (field.label || fieldPlaceholder(field)) }}</span>
                  </button>
                  <div v-if="attachmentItems(field).length" class="wecom-attachment-list">
                    <div
                      v-for="(item, index) in attachmentItems(field)"
                      :key="attachmentKey(item, index)"
                      class="wecom-attachment-chip"
                    >
                      <button
                        type="button"
                        class="wecom-attachment-chip__link"
                        :disabled="!item.url"
                        @click="openAttachment(item.url)"
                      >
                        <span>{{ attachmentDisplayName(item, index) }}</span>
                        <small v-if="attachmentSizeText(item.size)">{{ attachmentSizeText(item.size) }}</small>
                      </button>
                      <button
                        type="button"
                        class="wecom-attachment-chip__remove"
                        aria-label="移除附件"
                        @click="removeAttachment(field, index)"
                      >
                        移除
                      </button>
                    </div>
                  </div>
                </div>

                <button
                  v-else-if="controlType(field) === 'related_approval'"
                  type="button"
                  class="wecom-field-row wecom-related-trigger"
                  @click="selectRelatedApproval(field)"
                >
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <span class="wecom-field-value" :class="{ 'wecom-field-value--placeholder': !formData[field.code] }">
                    {{ formData[field.code] || fieldPlaceholder(field) }}
                    <ion-icon :icon="chevronForwardOutline" />
                  </span>
                </button>

                <div v-else-if="controlType(field) === 'detail'" class="wecom-detail-control">
                  <div class="wecom-detail-heading">
                    <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                      {{ field.label || field.code }}
                    </span>
                    <button type="button" class="wecom-detail-copy" @click="copyDetailRow(field)">复制</button>
                  </div>
                  <div
                    v-for="(row, index) in detailRowsForDisplay(field)"
                    :key="row.id || index"
                    class="wecom-detail-row"
                  >
                    <template v-if="detailChildFields(field).length">
                      <label v-for="child in detailChildFields(field)" :key="child.code">
                        <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(child) }">
                          {{ child.label }}
                        </span>
                        <select v-if="detailChildUsesOptions(child)" v-model="row[child.code]">
                          <option value="">请选择</option>
                          <option v-for="option in fieldOptions(child)" :key="option" :value="option">{{ option }}</option>
                        </select>
                        <input
                          v-else
                          v-model="row[child.code]"
                          :type="detailChildInputType(child)"
                          :inputmode="detailChildInputMode(child)"
                          :placeholder="fieldPlaceholder(child)"
                        />
                      </label>
                    </template>
                    <template v-else>
                      <input v-model="row.title" placeholder="标题" />
                      <input v-model="row.amount" inputmode="decimal" placeholder="金额" />
                    </template>
                  </div>
                  <button type="button" class="wecom-detail-add" @click="addDetailRow(field)">+ 添加明细</button>
                  <div v-if="detailSummaryEnabled(field)" class="wecom-detail-summary">
                    <span>{{ detailSummaryLabel(field) }}</span>
                    <strong :class="{ 'wecom-detail-summary-placeholder': !detailSummaryTotal(field) }">
                      {{ detailSummaryDisplayValue(field) }}
                    </strong>
                  </div>
                </div>

                <div v-else-if="controlType(field) === 'phone'" class="wecom-phone-control">
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <div>
                    <select v-model="formData[field.code].country_code">
                      <option value="+86">+86</option>
                      <option value="+852">+852</option>
                      <option value="+853">+853</option>
                      <option value="+886">+886</option>
                    </select>
                    <input v-model="formData[field.code].number" inputmode="tel" :placeholder="fieldPlaceholder(field)" />
                  </div>
                </div>

                <div v-else-if="controlType(field) === 'collection_account'" class="wecom-account-control">
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <input v-model="formData[field.code].account_name" placeholder="账户名" />
                  <input v-model="formData[field.code].bank_name" placeholder="开户行" />
                  <input v-model="formData[field.code].account_no" inputmode="numeric" placeholder="账号" />
                </div>

                <p v-else-if="controlType(field) === 'static_text'" class="wecom-static-text">
                  {{ field.placeholder || field.default_value || field.label || ' ' }}
                </p>

                <label v-else class="wecom-field-row">
                  <span class="wecom-field-label" :class="{ 'wecom-field-label--required': isRequired(field) }">
                    {{ field.label || field.code }}
                  </span>
                  <input v-model="formData[field.code]" class="wecom-row-input" :placeholder="fieldPlaceholder(field)" />
                </label>
              </template>
            </section>

            <section v-if="showHolidayGuide" class="wecom-holiday-note">
              <p v-for="line in holidayGuideLines" :key="line">{{ line }}</p>
            </section>

            <section v-if="showPunchCorrectionEligibilityPanel" class="punch-eligibility-section" aria-label="补卡资格">
              <div v-if="punchEligibilityLoading" class="punch-eligibility-state">
                <ion-spinner name="crescent" />
                <span>正在校验补卡资格</span>
              </div>
              <div v-else-if="punchEligibilityError" class="punch-eligibility-state is-danger">
                <strong>不可补卡</strong>
                <span>{{ punchEligibilityError }}</span>
              </div>
              <template v-else-if="punchEligibility">
                <div class="punch-eligibility-summary" :class="{ 'is-ok': punchEligibilityCanApply, 'is-danger': !punchEligibilityCanApply }">
                  <strong>{{ punchEligibilityCanApply ? '可补卡' : '不可补卡' }}</strong>
                  <span>{{ punchEligibility.message }}</span>
                </div>
                <div v-if="punchEligibilityData?.eligible_range || punchEligibilityData?.policy" class="punch-eligibility-meta">
                  <span v-if="punchEligibilityData?.eligible_range">
                    {{ punchEligibilityData.eligible_range.start_date }} 至 {{ punchEligibilityData.eligible_range.end_date }}
                  </span>
                  <span v-if="punchEligibilityData?.policy">
                    剩余 {{ punchEligibilityData.policy.remaining_count_in_month ?? '不限' }} 次
                  </span>
                </div>
                <div v-if="punchEligibilityCanApply" class="punch-eligibility-slots">
                  <button
                    v-for="slot in punchCorrectionSlotOptionsForField(punchCorrectionSlotField())"
                    :key="slot.key"
                    type="button"
                    :disabled="!slot.canApply"
                    :class="{ selected: selectedPunchCorrectionSlot?.key === slot.key, 'is-disabled': !slot.canApply }"
                    @click="selectPunchCorrectionSlot(slot)"
                  >
                    <strong>{{ slot.title }}</strong>
                    <span>{{ slot.canApply ? slot.subtitle : slot.disabledReason }}</span>
                  </button>
                </div>
                <div v-else class="punch-eligibility-denies">
                  <span v-for="item in punchEligibilityDenyReasons" :key="`${item.code}-${item.message}`">{{ item.message }}</span>
                </div>
              </template>
            </section>

            <section class="wecom-flow-section" aria-label="审批流程">
              <h2>审批流程</h2>
              <p>{{ mobileFlowPolicyText }}</p>
              <ion-spinner v-if="flowPreviewLoading" name="crescent" />
              <div v-else-if="flowPreviewError" class="wecom-flow-empty">{{ flowPreviewError }}</div>
              <div v-else-if="visibleFlowPreviewNodes.length" class="wecom-flow-list">
                <div v-for="node in visibleFlowPreviewNodes" :key="`${node.node_order}-${flowNodeTitle(node)}`" class="wecom-flow-step">
                  <span class="wecom-flow-dot">{{ flowNodeIcon(node) }}</span>
                  <div class="wecom-flow-body">
                    <strong>{{ flowNodeTitle(node) }}</strong>
                    <div class="wecom-flow-people">
                      <span v-for="person in flowNodePeople(node)" :key="person" class="wecom-person-chip">
                        <span class="wecom-person-avatar">{{ personInitial(person) }}</span>
                        <span>{{ person }}</span>
                      </span>
                      <button
                        v-if="canAppendFlowPerson(node)"
                        type="button"
                        class="wecom-person-add"
                        :aria-label="flowAppendButtonLabel(node)"
                        :title="flowAppendButtonLabel(node)"
                        @click="openFlowPersonPicker(node)"
                      >
                        <ion-icon :icon="addOutline" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="wecom-flow-empty">
                {{ flowPreview?.message || '当前模板未配置审批流程，请联系 HR 或管理员' }}
              </div>
            </section>

            <section class="wecom-template-creator">{{ templateCreatorText }}</section>
          </div>

          <div class="wecom-submit-bar">
            <button type="button" :disabled="submitting" @click="submitApplication">{{ primarySubmitLabel }}</button>
          </div>
        </section>

        <div v-if="orgPicker.visible" class="org-picker-backdrop" @click.self="closeOrgPicker">
          <section class="org-picker-sheet" role="dialog" aria-modal="true">
            <header class="org-picker-header">
              <div>
                <span>{{ orgPicker.kind === 'member' ? '选择员工' : '选择部门' }}</span>
                <strong>{{ orgPicker.title }}</strong>
              </div>
              <button type="button" @click="closeOrgPicker">关闭</button>
            </header>
            <div class="org-picker-body">
              <button
                v-for="row in visibleOrgRows"
                :key="row.key"
                type="button"
                class="org-picker-row"
                :class="{
                  'org-picker-row--employee': row.kind === 'employee',
                  'org-picker-row--selected': isOrgRowSelected(row),
                }"
                :style="{ paddingLeft: `${12 + row.level * 18}px` }"
                @click="handleOrgRowClick(row)"
              >
                <span
                  v-if="row.kind === 'department'"
                  class="org-picker-caret"
                  @click.stop="toggleOrgRow(row)"
                >
                  {{ row.expandable ? (row.expanded ? '▼' : '▶') : '' }}
                </span>
                <span v-else class="org-picker-caret" />
                <span class="org-picker-row-label">{{ row.label }}</span>
                <small v-if="row.caption">{{ row.caption }}</small>
              </button>
              <div v-if="!visibleOrgRows.length" class="org-picker-empty">
                暂无可选{{ orgPicker.kind === 'member' ? '员工' : '部门' }}
              </div>
            </div>
            <footer v-if="orgPicker.multiple" class="org-picker-footer">
              <span>已选 {{ arrayFieldValue({ code: orgPicker.fieldCode }).length }} 项</span>
              <button type="button" @click="closeOrgPicker">完成</button>
            </footer>
          </section>
        </div>

        <div v-if="companyPicker.visible" class="company-picker-backdrop" @click.self="cancelCompanyPicker">
          <section class="company-picker-sheet" role="dialog" aria-modal="true" aria-label="选择公司">
            <header class="company-picker-header">
              <button type="button" @click="cancelCompanyPicker">取消</button>
              <strong>{{ companyPicker.title || '所在公司' }}</strong>
              <button type="button" @click="confirmCompanyPicker">确定</button>
            </header>
            <div class="company-picker-body">
              <button
                v-for="company in companyOptions"
                :key="company.id"
                type="button"
                class="company-picker-row"
                :class="{ 'company-picker-row--selected': Number(company.id) === companyPicker.draftValue }"
                @click="companyPicker.draftValue = Number(company.id)"
              >
                <span class="company-picker-check">{{ Number(company.id) === companyPicker.draftValue ? '✓' : '' }}</span>
                <span>{{ companyLabel(company) }}</span>
              </button>
              <div v-if="!companyOptions.length" class="company-picker-empty">暂无可选公司</div>
            </div>
          </section>
        </div>

        <div v-if="leaveTypePicker.visible" class="leave-type-picker-backdrop" @click.self="cancelLeaveTypePicker">
          <section class="leave-type-picker-sheet" role="dialog" aria-modal="true" aria-label="选择请假类型">
            <header class="leave-type-picker-header">
              <button type="button" @click="cancelLeaveTypePicker">取消</button>
              <strong>{{ leaveTypePicker.title || '请假类型' }}</strong>
              <button type="button" @click="confirmLeaveTypePicker">确定</button>
            </header>
            <div class="leave-type-picker-body">
              <button
                v-for="option in sortedLeaveBalanceOptions"
                :key="option.leave_type_id"
                type="button"
                class="leave-type-picker-row"
                :class="{ 'leave-type-picker-row--selected': leaveTypePicker.draftValue === option.leave_type_name }"
                @click="leaveTypePicker.draftValue = option.leave_type_name"
              >
                <span>
                  <strong>{{ option.option_label }}</strong>
                  <small v-if="leaveTypeOptionCaption(option)">{{ leaveTypeOptionCaption(option) }}</small>
                </span>
                <span class="leave-type-picker-check">{{ leaveTypePicker.draftValue === option.leave_type_name ? '✓' : '' }}</span>
              </button>
              <div v-if="leaveBalanceLoading" class="leave-type-picker-empty">正在加载假期余额</div>
              <div v-else-if="leaveBalanceError" class="leave-type-picker-empty">{{ leaveBalanceError }}</div>
              <div v-else-if="!sortedLeaveBalanceOptions.length" class="leave-type-picker-empty">暂无可选假期类型</div>
            </div>
          </section>
        </div>

        <input
          ref="attachmentFileInput"
          class="approval-hidden-file-input"
          type="file"
          :accept="attachmentPicker.accept"
          :capture="attachmentPicker.capture || undefined"
          :multiple="attachmentPicker.multiple"
          @change="handleAttachmentFileChange"
        />

        <ion-modal
          :is-open="dateTimePicker.visible"
          class="approval-datetime-modal"
          @didDismiss="closeDateTimePicker"
        >
          <section class="approval-datetime-sheet" role="dialog" aria-modal="true">
            <header class="approval-datetime-header">
              <button type="button" @click="cancelDateTimePicker">取消</button>
              <strong>{{ dateTimePicker.title }}</strong>
              <button type="button" @click="confirmDateTimePicker">确定</button>
            </header>
            <ion-datetime
              class="approval-datetime-control"
              :value="dateTimePicker.draftValue"
              :presentation="dateTimePicker.presentation"
              :min="dateTimePicker.min || undefined"
              :max="dateTimePicker.max || undefined"
              :prefer-wheel="dateTimePicker.presentation === 'date-time'"
              locale="zh-CN"
              hour-cycle="h23"
              :first-day-of-week="1"
              @ionChange="handleDateTimeDraftChange"
            />
          </section>
        </ion-modal>

        <nav v-if="!selectedTemplate" class="approval-start-tabbar" aria-label="申请导航">
          <button class="approval-start-tabbar__item approval-start-tabbar__item--active" type="button">
            <ion-icon :icon="addCircle" />
            <span>发起申请</span>
          </button>
          <button class="approval-start-tabbar__item" type="button" @click="router.push('/app/tabs/approvals')">
            <ion-icon :icon="fileTrayOutline" />
            <span>我审批的</span>
          </button>
          <button class="approval-start-tabbar__item" type="button" @click="router.push('/app/tabs/approvals?segment=submitted')">
            <ion-icon :icon="arrowUpCircleOutline" />
            <span>已提交</span>
          </button>
        </nav>
      </div>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  IonButton,
  IonContent,
  IonDatetime,
  IonIcon,
  IonInput,
  IonItem,
  IonList,
  IonModal,
  IonPage,
  IonSelect,
  IonSelectOption,
  IonSpinner,
  IonTextarea,
  onIonViewWillEnter,
  toastController,
} from '@ionic/vue'
import {
  addCircle,
  addCircleOutline,
  addOutline,
  airplaneOutline,
  arrowUpCircleOutline,
  bagHandleOutline,
  calendarOutline,
  chevronBackOutline,
  chevronForwardOutline,
  fileTrayOutline,
  locationOutline,
  personAddOutline,
  searchOutline,
  shieldCheckmarkOutline,
  timeOutline,
} from 'ionicons/icons'
import { get, post, resolveAuthenticatedAssetUrl } from '@/utils/request'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const loading = ref(false)
const submitting = ref(false)
const templateGroups = ref<any[]>([])
const selectedTemplate = ref<any | null>(null)
const searchKeyword = ref('')
const flowPreview = ref<any | null>(null)
const flowPreviewLoading = ref(false)
const flowPreviewError = ref('')
let flowPreviewRequestId = 0
let flowPreviewTimer: ReturnType<typeof setTimeout> | null = null
const summary = ref('')
const formData = ref<Record<string, any>>({})
const punchEligibilityLoading = ref(false)
const punchEligibility = ref<any | null>(null)
const punchEligibilityError = ref('')
const punchEligibilityDate = ref('')
const leaveBalanceOptions = ref<LeaveBalanceOption[]>([])
const leaveBalanceLoading = ref(false)
const leaveBalanceError = ref('')
const employeeOptions = ref<any[]>([])
const departmentOptions = ref<any[]>([])
const companyOptions = ref<any[]>([])
const attachmentFileInput = ref<HTMLInputElement | null>(null)
const attachmentUploadingFieldCode = ref('')
const numericFieldTypes = new Set(['number', 'int', 'float', 'amount', 'formula', 'rating'])
const genericAttachmentAccept = [
  '.pdf',
  '.jpg',
  '.jpeg',
  '.png',
  '.gif',
  '.heic',
  '.heif',
  '.doc',
  '.docx',
  '.xls',
  '.xlsx',
  '.ppt',
  '.pptx',
  '.txt',
  '.csv',
].join(',')
type ApprovalAttachment = {
  name?: string
  filename?: string
  url: string
  size?: number
  content_type?: string
  uploaded_at?: string
}
type OrgPickerKind = 'member' | 'department'
type DateTimePresentation = 'date' | 'date-time'
type OrgPickerRow = {
  key: string
  kind: 'department' | 'employee'
  id: number
  label: string
  caption?: string
  level: number
  expandable?: boolean
  expanded?: boolean
}
const orgPicker = reactive({
  visible: false,
  kind: 'department' as OrgPickerKind,
  fieldCode: '',
  title: '',
  multiple: false,
  allowedMemberIds: [] as number[],
  restrictMembers: false,
})
const companyPicker = reactive({
  visible: false,
  fieldCode: '',
  title: '',
  draftValue: null as number | null,
})
const leaveTypePicker = reactive({
  visible: false,
  fieldCode: '',
  title: '',
  draftValue: '',
})
const dateTimePicker = reactive({
  visible: false,
  title: '',
  fieldCode: '',
  valueKey: '' as '' | 'start' | 'end',
  presentation: 'date' as DateTimePresentation,
  draftValue: '',
  min: '',
  max: '',
})
const attachmentPicker = reactive({
  fieldCode: '',
  accept: genericAttachmentAccept,
  multiple: true,
  capture: '' as '' | 'environment',
})
const expandedOrgKeys = ref<Set<string>>(new Set())
type ApprovalTemplateTile = {
  key: string
  name: string
  category: string
  businessCode: string
  codeAliases?: string[]
  icon: string
  color: 'yellow' | 'teal' | 'blue'
  source?: any
}
type ApprovalTemplateGridSlot = {
  key: string
  placeholder?: false
  template: ApprovalTemplateTile
} | {
  key: string
  placeholder: true
  template?: never
}
type PunchCorrectionSlotOption = {
  key: string
  value: string
  title: string
  subtitle: string
  shift: any
  punch: any
  punchType: 'check_in' | 'check_out'
  canApply: boolean
  disabledReason: string
}
type LeaveBalanceOption = {
  leave_type_id: number
  leave_type_code?: string | null
  leave_type_name: string
  leave_unit: string
  time_calc?: string
  hours_per_day: number
  rounding_direction?: string
  rounding_unit?: string
  quota_limited: boolean
  requires_proof: boolean
  total_days: number
  used_days: number
  remaining_days: number
  remaining_text: string
  balance_text: string
  option_label: string
  is_available: boolean
  sort_order: number
  reason?: string | null
}

const templateCatalog: Array<Omit<ApprovalTemplateTile, 'key' | 'source'>> = [
  { category: '人事', name: '法定节假日加班申请', businessCode: 'legal_overtime', codeAliases: ['overtime_holiday', 'holiday_overtime'], icon: timeOutline, color: 'blue' },
  { category: '人事', name: '请假', businessCode: 'leave', codeAliases: ['leave_request'], icon: calendarOutline, color: 'yellow' },
  { category: '人事', name: '出差', businessCode: 'business_trip', codeAliases: ['travel'], icon: airplaneOutline, color: 'blue' },
  { category: '人事', name: '外出', businessCode: 'outside', codeAliases: ['out', 'fieldwork'], icon: bagHandleOutline, color: 'blue' },
  { category: '人事', name: '离职', businessCode: 'resignation', icon: shieldCheckmarkOutline, color: 'teal' },
  { category: '人事', name: '招聘需求', businessCode: 'recruitment', codeAliases: ['recruitment_demand'], icon: personAddOutline, color: 'teal' },
  { category: '人事', name: '打卡补卡', businessCode: 'punch_correction', codeAliases: ['attendance_punch_correction'], icon: locationOutline, color: 'blue' },
]

const isPunchCorrectionSelected = computed(() => selectedTemplate.value ? isPunchCorrectionTemplate(selectedTemplate.value) : false)
const punchEligibilityData = computed(() => punchEligibility.value?.data || null)
const punchEligibilityCanApply = computed(() => Boolean(punchEligibility.value?.biz_success && punchEligibilityData.value?.can_apply))
const punchEligibilityDenyReasons = computed(() => {
  const reasons = punchEligibilityData.value?.deny_reasons
  return Array.isArray(reasons) ? reasons : []
})
const punchCorrectionSource = computed(() => routeQueryText('source'))
const isAttendanceAbnormalPunchCorrection = computed(() => {
  return isPunchCorrectionSelected.value && punchCorrectionSource.value.startsWith('attendance_')
})
const showPunchCorrectionEligibilityPanel = computed(() => {
  return isAttendanceAbnormalPunchCorrection.value && Boolean(punchEligibilityLoading.value || punchEligibilityError.value || punchEligibility.value)
})
const punchCorrectionMonthUsedCount = computed(() => {
  const data = punchEligibilityData.value || {}
  const policy = data.policy || {}
  const candidates = [
    policy.used_count_in_month,
    policy.monthly_used_count,
    data.used_count_in_month,
    data.monthly_used_count,
  ]
  const matched = candidates.find((item) => item !== undefined && item !== null && item !== '')
  const count = Number(matched)
  return Number.isFinite(count) ? count : 0
})
const selectedPunchCorrectionSlot = computed(() => {
  const field = punchCorrectionSlotField()
  if (!field) return null
  const current = String(formData.value[field.code] || '')
  return punchCorrectionSlotOptionsForField(field).find((item) => item.value === current) || null
})
const sortedLeaveBalanceOptions = computed(() => {
  return [...leaveBalanceOptions.value]
    .filter((item) => item.is_available !== false)
    .sort((a, b) => (Number(a.sort_order || 99) - Number(b.sort_order || 99)) || Number(a.leave_type_id || 0) - Number(b.leave_type_id || 0))
})
const requiredFields = computed(() => (selectedTemplate.value ? visibleTemplateFields(selectedTemplate.value).filter(isRequired) : []))
const completedRequiredCount = computed(() => {
  return requiredFields.value.filter((field: any) => hasValue(formData.value[field.code])).length
})
const missingRequiredField = computed(() => {
  if (!selectedTemplate.value) return null
  return visibleTemplateFields(selectedTemplate.value).find((field: any) => isRequired(field) && !hasValue(formData.value[field.code])) || null
})
const canSubmit = computed(() => Boolean(selectedTemplate.value) && !missingRequiredField.value)
const formReadinessText = computed(() => {
  if (!requiredFields.value.length) return '这个模板没有必填字段'
  if (!missingRequiredField.value) return '必填信息已完成'
  return `还差：${missingRequiredField.value.label || missingRequiredField.value.code}`
})
const submitTip = computed(() => {
  if (!selectedTemplate.value) return '请先选择申请模板。'
  if (missingRequiredField.value) return `请先填写 ${missingRequiredField.value.label || missingRequiredField.value.code}。`
  return '信息完整，可以提交审批。'
})
const submitLabel = computed(() => {
  if (submitting.value) return '提交中...'
  if (!canSubmit.value) return '先补全必填项'
  return '提交申请'
})
const visibleFlowPreviewNodes = computed(() => {
  const nodes = Array.isArray(flowPreview.value?.nodes) ? flowPreview.value.nodes : []
  return nodes.filter((node: any) => shouldDisplayFlowPreviewNode(node))
})
const flowPreviewSubtitle = computed(() => {
  if (flowPreviewLoading.value) return '正在匹配审批节点'
  if (flowPreview.value?.has_flow && visibleFlowPreviewNodes.value.length) return `${visibleFlowPreviewNodes.value.length} 个节点`
  return '提交前可先确认处理人'
})
const mobileFlowPolicyText = computed(() => {
  const nodes = visibleFlowPreviewNodes.value
  if (!nodes.length) return '审批人和抄送人按流程设计自动匹配'
  return '已由管理员预设不可修改审批人和删除抄送人'
})
const primarySubmitLabel = computed(() => submitting.value ? '提交中...' : '提交')
const templateNoticeText = computed(() => {
  if (!selectedTemplate.value) return ''
  if (isLegalOvertimeTemplate(selectedTemplate.value)) return '仅限法定节假日使用该申请'
  return selectedTemplate.value.description || selectedTemplate.value.remark || '请按模板要求填写申请信息'
})
const showHolidayGuide = computed(() => selectedTemplate.value ? isLegalOvertimeTemplate(selectedTemplate.value) : false)
const holidayGuideLines = [
  '法定节假日如下：',
  '新年（元旦）：1月1日',
  '春节：农历正月初一、初二、初三',
  '清明节：农历清明当日',
  '劳动节：5月1-2日',
  '端午节：农历端午当日',
  '中秋节：农历中秋当日',
  '国庆节：10月1日、2日、3日',
]
const templateCreatorText = computed(() => {
  const template = selectedTemplate.value || {}
  const creator = template.creator_name || template.created_by_name || template.creator?.name || template.owner_name || '管理员'
  return `此模板由 ${creator} 创建`
})
const departmentNameById = computed(() => {
  const map = new Map<number, string>()
  for (const department of departmentOptions.value) {
    map.set(Number(department.id), department.name || `部门 ${department.id}`)
  }
  return map
})
const departmentTree = computed(() => buildDepartmentTree(departmentOptions.value))
const employeeByDepartment = computed(() => {
  const groups = new Map<number, any[]>()
  const allowed = new Set(orgPicker.allowedMemberIds.map(Number).filter(Boolean))
  for (const employee of employeeOptions.value) {
    if (orgPicker.kind === 'member' && orgPicker.restrictMembers && !allowed.has(Number(employee.id))) continue
    const departmentId = Number(employee.department_id || 0)
    if (!groups.has(departmentId)) groups.set(departmentId, [])
    groups.get(departmentId)!.push(employee)
  }
  for (const employees of groups.values()) {
    employees.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN'))
  }
  return groups
})
const visibleOrgRows = computed<OrgPickerRow[]>(() => {
  const rows: OrgPickerRow[] = []
  const visitDepartment = (department: any, level: number) => {
    const id = Number(department.id)
    const key = `department-${id}`
    const children = department.children || []
    const employees = employeeByDepartment.value.get(id) || []
    const expandable = children.length > 0 || (orgPicker.kind === 'member' && employees.length > 0)
    const expanded = expandedOrgKeys.value.has(key)
    rows.push({
      key,
      kind: 'department',
      id,
      label: department.name || department.department_name || `部门 ${id}`,
      caption: orgPicker.kind === 'member' && employees.length ? `${employees.length} 人` : '',
      level,
      expandable,
      expanded,
    })
    if (!expanded) return
    for (const child of children) visitDepartment(child, level + 1)
    if (orgPicker.kind === 'member') {
      for (const employee of employees) {
        rows.push({
          key: `employee-${employee.id}`,
          kind: 'employee',
          id: Number(employee.id),
          label: employee.name || employee.employee_no || `员工 ${employee.id}`,
          caption: employee.position || employee.employee_no || '',
          level: level + 1,
        })
      }
    }
  }

  for (const department of departmentTree.value) visitDepartment(department, 0)
  if (orgPicker.kind === 'member') {
    const unassigned = employeeByDepartment.value.get(0) || []
    for (const employee of unassigned) {
      rows.push({
        key: `employee-${employee.id}`,
        kind: 'employee',
        id: Number(employee.id),
        label: employee.name || employee.employee_no || `员工 ${employee.id}`,
        caption: '未分配部门',
        level: 0,
      })
    }
  }
  return rows
})
const visibleTemplateGroups = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  return mergedTemplateGroups()
    .map((group) => ({
      ...group,
      templates: keyword
        ? group.templates.filter((template) => template.name.toLowerCase().includes(keyword))
        : group.templates,
    }))
    .filter((group) => group.templates.length > 0)
})

async function presentToast(message: string, color: 'success' | 'danger' | 'warning' = 'success') {
  const toast = await toastController.create({ message, duration: 1800, color })
  await toast.present()
}

async function loadTemplates() {
  loading.value = true
  try {
    templateGroups.value = await get<any[]>('/approval/templates')
  } catch {
    templateGroups.value = []
    await presentToast('审批模板加载失败', 'danger')
  } finally {
    loading.value = false
  }
}

function normalizeTemplateKey(value: unknown) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '_')
}

function isAttendanceRuleBindingTemplate(template: any) {
  return templateBusinessCode(template).startsWith('attendance_rule_')
}

function routeQueryText(key: string) {
  const value = route.query[key]
  if (Array.isArray(value)) return String(value[0] || '').trim()
  return String(value || '').trim()
}

function approvalReturnTarget() {
  const target = routeQueryText('return_to')
  if (target === '/app/attendance?tab=apply' || target === 'attendance_apply') return '/app/attendance?tab=apply'
  if (target === '/app/tabs/approvals' || target === 'approvals') return '/app/tabs/approvals'
  if (target === '/app/tabs/approvals?segment=submitted' || target === 'submitted') {
    return '/app/tabs/approvals?segment=submitted'
  }
  if (routeQueryText('source').startsWith('attendance_')) return '/app/attendance?tab=apply'
  return ''
}

function goBackFromApprovalStart() {
  const target = approvalReturnTarget()
  if (target) {
    void router.replace(target)
    return
  }
  if (window.history.length > 1) {
    router.back()
    return
  }
  void router.replace('/app/tabs/approvals')
}

function goBackFromSelectedTemplate() {
  if (approvalReturnTarget()) {
    goBackFromApprovalStart()
    return
  }
  closeSelectedTemplate()
}

function templateBusinessCode(template: any) {
  return normalizeTemplateKey(template?.business_code || template?.code || template?.key || template?.name)
}

function templateCategory(group: any) {
  return String(group?.category || group?.name || '通用审批').trim()
}

function templateGridSlots(templates: ApprovalTemplateTile[]): ApprovalTemplateGridSlot[] {
  const slots: ApprovalTemplateGridSlot[] = templates.map((template) => ({
    key: template.key,
    template,
  }))
  const remainder = templates.length % 3
  if (!remainder) return slots
  const placeholders = 3 - remainder
  for (let index = 0; index < placeholders; index += 1) {
    slots.push({
      key: `placeholder-${templates[0]?.category || 'group'}-${templates.length}-${index}`,
      placeholder: true,
    })
  }
  return slots
}

function matchedCatalogItem(template: any, category: string) {
  const code = templateBusinessCode(template)
  const name = String(template?.name || '').trim()
  return templateCatalog.find((item) => {
    const codes = [item.businessCode, ...(item.codeAliases || [])].map(normalizeTemplateKey)
    return codes.includes(code) || (item.category === category && item.name === name)
  })
}

function fallbackColor(category: string): ApprovalTemplateTile['color'] {
  if (category === '假勤管理' || category === '人事') return 'blue'
  if (category === '人事管理' || category === '行政' || category === '行政管理') return 'teal'
  return 'yellow'
}

function fallbackIcon(category: string) {
  if (category === '假勤管理' || category === '人事') return calendarOutline
  if (category === '行政' || category === '行政管理') return personAddOutline
  return calendarOutline
}

function isHiddenMobileApprovalTemplate(template: any) {
  const code = templateBusinessCode(template)
  const name = String(template?.name || '').trim()
  return ['headcount_request', 'headcount_plan'].includes(code) || name === '用人审批'
}

function mergedTemplateGroups() {
  const groups = new Map<string, ApprovalTemplateTile[]>()
  const ensureGroup = (category: string) => {
    if (!groups.has(category)) groups.set(category, [])
    return groups.get(category)!
  }
  const addTile = (tile: ApprovalTemplateTile) => {
    if (tile.source) {
      for (const list of groups.values()) {
        const fallbackIndex = list.findIndex((item) => !item.source && (
          item.businessCode === tile.businessCode || item.name === tile.name
        ))
        if (fallbackIndex >= 0) {
          list.splice(fallbackIndex, 1)
        }
      }
    }
    const list = ensureGroup(tile.category)
    const existingIndex = list.findIndex((item) => {
      return item.businessCode === tile.businessCode || item.name === tile.name
    })
    if (existingIndex >= 0) {
      list[existingIndex] = { ...list[existingIndex], ...tile }
    } else {
      list.push(tile)
    }
  }

  // Only published server templates can be started.

  for (const group of templateGroups.value) {
    const category = templateCategory(group)
    for (const template of groupTemplates(group)) {
      if (isAttendanceRuleBindingTemplate(template)) continue
      if (isHiddenMobileApprovalTemplate(template)) continue
      const catalogItem = matchedCatalogItem(template, category)
      const name = String(template?.name || catalogItem?.name || '未命名申请').trim()
      const businessCode = templateBusinessCode(template)
      addTile({
        key: `template-${template?.id || businessCode || name}`,
        name,
        category: category || '售后现场',
        businessCode: catalogItem?.businessCode || businessCode || normalizeTemplateKey(name),
        icon: catalogItem?.icon || fallbackIcon(category),
        color: catalogItem?.color || fallbackColor(category),
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

async function openTemplate(template: ApprovalTemplateTile) {
  if (template.source) {
    await router.replace({
      path: '/app/approval/start',
      query: {
        ...route.query,
        business_code: template.businessCode || templateBusinessCode(template.source),
      },
    })
    await selectTemplate(template.source)
    return
  }
  await presentToast('该申请模板暂未启用，请联系 HR 或管理员', 'warning')
}

async function openTemplateFromRoute() {
  const queryCode = normalizeTemplateKey(routeQueryText('business_code'))
  if (!queryCode) return
  const tiles = mergedTemplateGroups().flatMap((group) => group.templates)
  const matched = tiles.find((template) => {
    const codes = [
      template.businessCode,
      template.name,
      template.source?.business_code,
      template.source?.code,
      template.source?.key,
    ].map(normalizeTemplateKey)
    return codes.includes(queryCode)
  })
  if (!matched) return
  await openTemplate(matched)
}

async function refreshSelectedTemplateFromLatestGroups() {
  if (!selectedTemplate.value) return
  const selectedId = Number(selectedTemplate.value.id || 0)
  const selectedCode = templateBusinessCode(selectedTemplate.value)
  const tiles = mergedTemplateGroups().flatMap((group) => group.templates)
  const matched = tiles.find((template) => {
    if (!template.source) return false
    if (selectedId && Number(template.source?.id || 0) === selectedId) return true
    return templateBusinessCode(template.source) === selectedCode
  })
  if (!matched?.source) {
    closeSelectedTemplate()
    return
  }
  await selectTemplate(matched.source)
}

async function selectTemplate(template: any) {
  selectedTemplate.value = template
  flowPreview.value = null
  flowPreviewError.value = ''
  summary.value = ''
  punchEligibility.value = null
  punchEligibilityError.value = ''
  punchEligibilityDate.value = ''
  leaveBalanceError.value = ''
  if (!isLeaveTemplate(template)) {
    leaveBalanceOptions.value = []
  }
  const next: Record<string, any> = {}
  for (const field of templateFields(template)) {
    next[field.code] = defaultFormValue(field)
  }
  formData.value = next
  await Promise.all([
    loadOrganizationOptionsForTemplate(template),
    loadLeaveBalanceOptionsForTemplate(template),
    loadFlowPreview(template),
  ])
  await applyRoutePrefillToSelectedTemplate()
}

async function applyRoutePrefillToSelectedTemplate() {
  if (!selectedTemplate.value) return
  const targetDate = routeQueryText('attendance_date') || routeQueryText('target_date') || routeQueryText('correction_date')
  const statusText = routeQueryText('attendance_status')
  const punchType = routeQueryText('punch_type')
  if (statusText && !summary.value.trim()) {
    summary.value = targetDate ? `${targetDate} ${statusText}` : statusText
  }
  for (const field of templateFields(selectedTemplate.value)) {
    const value = routeQueryText(field.code)
    if (!value) continue
    const type = controlType(field)
    if (['number', 'amount', 'department', 'member', 'company', 'location'].includes(type)) {
      const numeric = Number(value)
      formData.value[field.code] = Number.isFinite(numeric) ? numeric : value
    } else {
      formData.value[field.code] = value
    }
  }

  if (!isPunchCorrectionTemplate(selectedTemplate.value)) return
  const dateField = punchCorrectionDateField()
  if (targetDate && dateField) {
    formData.value[dateField.code] = targetDate.slice(0, 10)
    if (isAttendanceAbnormalPunchCorrection.value) {
      await refreshPunchCorrectionEligibility(targetDate)
    }
  }
  const slotField = punchCorrectionSlotField()
  if (slotField && punchType && isAttendanceAbnormalPunchCorrection.value) {
    const matchedSlot = punchCorrectionSlotOptionsForField(slotField)
      .find((item) => item.punchType === punchType && item.canApply)
    if (matchedSlot) {
      applyPunchCorrectionSlot(matchedSlot)
    }
  }
}

function closeSelectedTemplate() {
  selectedTemplate.value = null
  flowPreview.value = null
  flowPreviewError.value = ''
  summary.value = ''
  punchEligibility.value = null
  punchEligibilityError.value = ''
  punchEligibilityDate.value = ''
  leaveBalanceError.value = ''
  leaveTypePicker.visible = false
}

async function loadLeaveBalanceOptionsForTemplate(template: any = selectedTemplate.value) {
  if (!template || !isLeaveTemplate(template)) return
  const templateId = Number(template.id || 0)
  leaveBalanceLoading.value = true
  leaveBalanceError.value = ''
  try {
    const year = new Date().getFullYear()
    const options = await get<LeaveBalanceOption[]>('/leave/balances/me/options', { year })
    if (!selectedTemplate.value || Number(selectedTemplate.value.id || 0) !== templateId) return
    leaveBalanceOptions.value = Array.isArray(options) ? options : []
    if (!leaveBalanceOptions.value.length) {
      leaveBalanceError.value = '暂无可选假期余额，请联系 HR 维护假期管理'
    }
  } catch {
    if (!selectedTemplate.value || Number(selectedTemplate.value.id || 0) !== templateId) return
    leaveBalanceOptions.value = []
    leaveBalanceError.value = '假期余额加载失败，请稍后重试'
  } finally {
    if (selectedTemplate.value && Number(selectedTemplate.value.id || 0) === templateId) {
      leaveBalanceLoading.value = false
    }
  }
}

async function loadFlowPreview(template: any, options: { silent?: boolean } = {}) {
  if (!template?.id) return
  const templateId = Number(template.id)
  const requestId = ++flowPreviewRequestId
  if (!options.silent) {
    flowPreviewLoading.value = true
  }
  flowPreviewError.value = ''
  try {
    const preview = await post<any>(`/approval/types/${templateId}/flow-preview`, {
      form_data: normalizedFormData(),
    })
    if (selectedTemplate.value?.id !== template.id || requestId !== flowPreviewRequestId) return
    flowPreview.value = preview
    if (!preview?.has_flow) {
      flowPreviewError.value = preview?.message || '当前模板未配置审批流程，请联系 HR 或管理员'
    }
  } catch {
    if (selectedTemplate.value?.id !== template.id || requestId !== flowPreviewRequestId) return
    flowPreview.value = null
    flowPreviewError.value = '审批流程加载失败，请稍后重试'
  } finally {
    if (selectedTemplate.value?.id === template.id && requestId === flowPreviewRequestId) {
      flowPreviewLoading.value = false
    }
  }
}

function scheduleFlowPreviewReload() {
  if (flowPreviewTimer) {
    clearTimeout(flowPreviewTimer)
  }
  if (!selectedTemplate.value?.id) return
  flowPreviewTimer = setTimeout(() => {
    flowPreviewTimer = null
    if (selectedTemplate.value?.id) {
      void loadFlowPreview(selectedTemplate.value, { silent: true })
    }
  }, 250)
}

function scrollToTemplates() {
  document.querySelector('.approval-template-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function groupTemplates(group: any) {
  return group?.types || group?.templates || []
}

function isLegalOvertimeTemplate(template: any) {
  const code = templateBusinessCode(template)
  const name = String(template?.name || '').trim()
  return code === 'legal_overtime' || code === 'holiday_overtime' || code === 'overtime_holiday' || name.includes('法定节假日加班')
}

function isPunchCorrectionTemplate(template: any) {
  const code = templateBusinessCode(template)
  const name = String(template?.name || '').trim()
  return ['punch_correction', 'attendance_punch_correction'].includes(code) || name.includes('打卡补卡')
}

function isLeaveTemplate(template: any) {
  const code = templateBusinessCode(template)
  const name = String(template?.name || '').trim()
  return ['leave', 'leave_request'].includes(code) || name === '请假' || name.includes('请假')
}

function fallbackTemplateFields(template: any) {
  if (isPunchCorrectionTemplate(template)) {
    return [
      { code: 'correction_date', label: '补卡日期', field_type: 'date', is_required: true, placeholder: '请选择日期' },
      { code: 'punch_correction_slot', label: '补卡班次', field_type: 'select', is_required: false, placeholder: '请选择', display_condition: 'source=attendance_abnormal', options_json: { attendance_component: 'punch_correction', context_only: true, options: ['上班卡', '下班卡'] } },
      { code: 'punch_time', label: '补卡时间', field_type: 'datetime', is_required: true, placeholder: '请选择时间' },
      { code: 'reason', label: '补卡事由', field_type: 'textarea', is_required: true, placeholder: '请输入' },
      { code: 'attachment', label: '说明附件', field_type: 'attachment', is_required: false, placeholder: '上传附件' },
    ]
  }
  if (isBusinessTripTemplate(template)) {
    return [
      { code: 'department', label: '部门', field_type: 'department', is_required: true, placeholder: '请选择' },
      { code: 'project_name', label: '项目名称', field_type: 'select', is_required: true, placeholder: '请选择', options_json: { options: ['项目一', '项目二', '项目三'] } },
      { code: 'trip_reason', label: '出差事由', field_type: 'textarea', is_required: true, placeholder: '请输入' },
      { code: 'trip_location', label: '出差地点', field_type: 'text', is_required: true, placeholder: '请输入此行程涉及的所有城市' },
      { code: 'trip_duration', label: '出差时长', field_type: 'duration', is_required: true, placeholder: '0天', options_json: { time_scale: 'day', unit_hours: 24, duration_mode: 'natural_day', attendance_sync: true } },
      { code: 'trip_expense_detail', label: '明细', field_type: 'detail', is_required: true, placeholder: '添加明细', options_json: { child_fields: [
        { code: 'flight_ticket', label: '机票', field_type: 'amount', is_required: true, placeholder: '请输入' },
        { code: 'train_ticket', label: '火车票', field_type: 'amount', is_required: true, placeholder: '请输入' },
        { code: 'lodging', label: '住宿', field_type: 'amount', is_required: true, placeholder: '请输入' },
        { code: 'trip_allowance', label: '出差补助', field_type: 'amount', is_required: true, placeholder: '请输入' },
        { code: 'local_transport', label: '市内交通', field_type: 'amount', is_required: true, placeholder: '请输入' },
      ], summary: { enabled: true, label: '预计出差费用合计', field_codes: ['flight_ticket', 'train_ticket', 'lodging', 'trip_allowance', 'local_transport'], value_type: 'amount' }, print_layout: 'multi_line' } },
      { code: 'attachment', label: '附件', field_type: 'attachment', is_required: false, placeholder: '上传附件' },
      { code: 'description', label: '说明', field_type: 'textarea', is_required: false, placeholder: '请输入' },
    ]
  }
  if (isLeaveTemplate(template)) {
    return [
      { code: 'company', label: '所在公司', field_type: 'company', is_required: true, placeholder: '请选择' },
      { code: 'leave_type', label: '请假类型', field_type: 'select', is_required: true, placeholder: '请选择', options_json: { data_source: 'leave_balance', label_field: 'option_label', value_field: 'leave_type_name', attendance_component: 'leave' } },
      { code: 'duration', label: '请假时长', field_type: 'duration', is_required: true, placeholder: '0小时', options_json: { time_scale: 'hour', unit_hours: 8, duration_mode: 'workday', attendance_component: 'leave' } },
      { code: 'reason', label: '请假事由', field_type: 'textarea', is_required: false, placeholder: '请输入请假事由' },
      { code: 'attachment', label: '说明附件', field_type: 'attachment', is_required: false, placeholder: '上传附件' },
    ]
  }
  const businessCode = templateBusinessCode(template)
  const name = String(template?.name || '').trim()
  if (businessCode === 'outside' || businessCode === 'out' || businessCode === 'fieldwork' || name === '外出') {
    return [
      { code: 'department', label: '部门', field_type: 'department', is_required: true, placeholder: '请选择' },
      { code: 'reason', label: '外出事由', field_type: 'textarea', is_required: true, placeholder: '请输入' },
      { code: 'outside_location', label: '外出地点', field_type: 'text', is_required: true, placeholder: '请输入' },
      { code: 'outside_duration', label: '外出时长', field_type: 'duration', is_required: true, placeholder: '0小时', options_json: { time_scale: 'hour', unit_hours: 8, duration_mode: 'workday', attendance_component: 'outside' } },
      { code: 'attachment', label: '附件', field_type: 'attachment', is_required: false, placeholder: '上传附件' },
    ]
  }
  if (businessCode === 'resignation' || name === '离职' || name === '离职申请') {
    return [
      { code: 'department', label: '所在部门', field_type: 'department', is_required: true, placeholder: '请选择' },
      { code: 'position', label: '职位', field_type: 'text', is_required: false, placeholder: '请填写' },
      { code: 'entry_date', label: '入职日期', field_type: 'date', is_required: false, placeholder: '请选择' },
      { code: 'resignation_apply_date', label: '申请离职日期', field_type: 'date', is_required: false, placeholder: '请选择' },
      { code: 'expected_resignation_date', label: '预计离职日期', field_type: 'date', is_required: true, placeholder: '请选择' },
      { code: 'resignation_reason', label: '离职原因', field_type: 'textarea', is_required: true, placeholder: '请输入' },
      { code: 'attachment', label: '附件', field_type: 'attachment', is_required: false, placeholder: '上传附件' },
    ]
  }
  if (businessCode === 'recruitment' || businessCode === 'recruitment_demand' || name === '招聘需求') {
    return [
      { code: 'recruitment_department', label: '招聘部门', field_type: 'department', is_required: true, placeholder: '请选择' },
      { code: 'applicant', label: '申请人', field_type: 'text', is_required: true, placeholder: '请填写' },
      { code: 'apply_date', label: '申请日期', field_type: 'date', is_required: false, placeholder: '请选择' },
      { code: 'position_name', label: '招聘岗位', field_type: 'text', is_required: true, placeholder: '请输入' },
      { code: 'headcount', label: '拟招聘人数', field_type: 'number', is_required: true, placeholder: '请输入' },
      { code: 'expected_arrival_date', label: '期望到岗日期', field_type: 'date', is_required: false, placeholder: '请选择' },
      { code: 'responsibilities', label: '岗位职责', field_type: 'textarea', is_required: false, placeholder: '请输入' },
      { code: 'requirements', label: '任职要求', field_type: 'textarea', is_required: false, placeholder: '请输入' },
      { code: 'attachment', label: '附件', field_type: 'attachment', is_required: false, placeholder: '上传附件' },
    ]
  }
  if (!isLegalOvertimeTemplate(template)) return []
  return [
    { code: 'applicant', label: '申请人', field_type: 'text', is_required: true, placeholder: '请填写' },
    { code: 'department', label: '申请部门', field_type: 'department', is_required: true, placeholder: '请选择' },
    {
      code: 'reason',
      label: '加班事由',
      field_type: 'textarea',
      is_required: true,
      placeholder: '请详细说明加班事由（包括地点、工作具体内容等）',
    },
    {
      code: 'overtime_duration',
      label: '加班时长',
      field_type: 'duration',
      is_required: true,
      options_json: { time_scale: 'hour', unit_hours: 8, duration_mode: 'workday', attendance_component: 'overtime', follows_attendance_overtime_rule: true },
      placeholder: '0小时',
    },
  ]
}

function templateFields(template: any) {
  const fields = Array.isArray(template?.fields) ? template.fields : []
  const formFields = Array.isArray(template?.form_fields) ? template.form_fields : []
  const configuredFields = fields.length ? fields : formFields
  const resolvedFields = configuredFields.length ? configuredFields : fallbackTemplateFields(template)
  return normalizeBusinessTripDetailFields(template, normalizePunchCorrectionFields(template, resolvedFields))
}

function shouldShowPunchCorrectionSlotField(field: any) {
  if (!isPunchCorrectionSlotField(field)) return true
  return isAttendanceAbnormalPunchCorrection.value
}

function shouldShowTemplateField(field: any) {
  if (isPunchCorrectionSlotField(field)) return shouldShowPunchCorrectionSlotField(field)
  return true
}

function visibleTemplateFields(template: any) {
  return templateFields(template).filter(shouldShowTemplateField)
}

function controlType(field: any) {
  return String(field.field_type || 'text').toLowerCase()
}

const detailSummaryFieldTypes = new Set(['number', 'int', 'float', 'amount', 'formula'])
const businessTripExpenseFieldCodes = ['flight_ticket', 'train_ticket', 'lodging', 'trip_allowance', 'local_transport']

function isBusinessTripTemplate(template: any) {
  const code = templateBusinessCode(template)
  return code === 'business_trip' || code === 'travel'
}

function cloneTemplateField(field: any) {
  return field && typeof field === 'object'
    ? JSON.parse(JSON.stringify(field))
    : field
}

function looksLikePunchCorrectionDateField(field: any) {
  const text = compactFieldText(field)
  return text.includes('correction_date') || text.includes('target_date') || text.includes('punch_date') || text.includes('补卡日期')
}

function looksLikePunchCorrectionSlotField(field: any) {
  const text = compactFieldText(field)
  return text.includes('punch_correction_slot') || text.includes('punch_type') || text.includes('shift') || text.includes('补卡班次') || text.includes('补卡卡点')
}

function looksLikePunchCorrectionTimeField(field: any) {
  const text = compactFieldText(field)
  return text.includes('punch_time') || text.includes('correction_time') || text.includes('补卡时间')
}

function looksLikePunchCorrectionReasonField(field: any) {
  const text = compactFieldText(field)
  return text.includes('punch_reason') || text.includes('reason') || text.includes('补卡原因') || text.includes('事由') || text.includes('说明') || text.includes('备注')
}

function defaultPunchCorrectionFields() {
  return {
    date: { code: 'correction_date', label: '补卡日期', field_type: 'date', is_required: true, placeholder: '请选择日期' },
    slot: { code: 'punch_correction_slot', label: '补卡班次', field_type: 'select', is_required: false, placeholder: '请选择', display_condition: 'source=attendance_abnormal', options_json: { attendance_component: 'punch_correction', context_only: true, options: ['上班卡', '下班卡'] } },
    time: { code: 'punch_time', label: '补卡时间', field_type: 'datetime', is_required: true, placeholder: '请选择时间' },
    reason: { code: 'reason', label: '补卡事由', field_type: 'textarea', is_required: true, placeholder: '请输入' },
  }
}

function normalizePunchCorrectionFields(template: any, fields: any[]) {
  if (!isPunchCorrectionTemplate(template)) return fields
  const normalized = fields.map(cloneTemplateField)
  const defaults = defaultPunchCorrectionFields()
  const hasDate = normalized.some(looksLikePunchCorrectionDateField)
  const hasSlot = normalized.some(looksLikePunchCorrectionSlotField)
  const hasTime = normalized.some(looksLikePunchCorrectionTimeField)
  const hasReason = normalized.some(looksLikePunchCorrectionReasonField)
  if (hasDate && hasSlot && hasTime && hasReason) return normalized

  const missingHeadFields = []
  if (!hasDate) missingHeadFields.push(defaults.date)
  if (!hasSlot) missingHeadFields.push(defaults.slot)
  if (!hasTime) missingHeadFields.push(defaults.time)

  if (missingHeadFields.length) {
    const timeIndex = normalized.findIndex(looksLikePunchCorrectionTimeField)
    normalized.splice(timeIndex >= 0 ? timeIndex : normalized.length, 0, ...missingHeadFields)
  }
  if (!hasReason) normalized.push(defaults.reason)
  return normalized
}

function normalizeBusinessTripDetailFields(template: any, fields: any[]) {
  if (!isBusinessTripTemplate(template)) return fields
  if (fields.some((field: any) => field?.code === 'trip_expense_detail' && controlType(field) === 'detail')) {
    return fields
  }
  const expenseFields = businessTripExpenseFieldCodes
    .map((code) => fields.find((field: any) => field?.code === code))
    .filter(Boolean)
    .map(cloneTemplateField)
  if (!expenseFields.length) return fields

  const detailInsertIndex = fields.findIndex((field: any) => (
    field?.code === 'detail_header' || businessTripExpenseFieldCodes.includes(field?.code)
  ))
  const normalized = fields.filter((field: any) => (
    field?.code !== 'detail_header' && !businessTripExpenseFieldCodes.includes(field?.code)
  ))
  normalized.splice(Math.max(detailInsertIndex, 0), 0, {
    code: 'trip_expense_detail',
    label: '明细',
    field_type: 'detail',
    is_required: expenseFields.some((field: any) => isRequired(field)),
    placeholder: '添加明细',
    print_visible: true,
    printable: true,
    is_business_calculation: true,
    options_json: {
      child_fields: expenseFields,
      summary: {
        enabled: true,
        label: '预计出差费用合计',
        field_codes: expenseFields.map((field: any) => field.code).filter(Boolean),
        value_type: 'amount',
      },
      print_layout: 'multi_line',
    },
  })
  return normalized
}

function isLongTextField(field: any) {
  const type = controlType(field)
  const label = String(field.label || field.code || '')
  return type === 'textarea' || (type === 'text' && /事由|原因|说明|备注/.test(label))
}

function isSimpleInputField(field: any) {
  const type = controlType(field)
  return numericFieldTypes.has(type) || ['text', 'identity_card', 'external_contact', 'industry_department', 'province_city', 'invoice', 'customer', 'budget_request', 'related_contract', 'engineering_project'].includes(type)
}

function isDateTimeField(field: any) {
  return ['date', 'datetime'].includes(controlType(field))
}

function ionInputType(field: any): 'text' | 'number' | 'date' | 'datetime-local' {
  const type = controlType(field)
  if (numericFieldTypes.has(type)) return 'number'
  if (type === 'date') return 'date'
  if (type === 'datetime') return 'datetime-local'
  return 'text'
}

function fieldLabel(field: any) {
  return `${field.label || field.code}${isRequired(field) ? ' *' : ''}`
}

function fieldPlaceholder(field: any) {
  if (isPunchCorrectionSlotField(field)) {
    if (!punchCorrectionDateValue()) return '请先选择日期'
    if (punchEligibilityLoading.value) return '校验中'
    if (!punchEligibilityCanApply.value) return '不可补卡'
    return '请选择'
  }
  if (isPunchCorrectionTimeField(field) && isAttendanceAbnormalPunchCorrection.value && !selectedPunchCorrectionSlot.value) return '请先选择班次'
  if (field.placeholder) return field.placeholder
  const type = controlType(field)
  if (type === 'date' || type === 'date_range') return '请选择日期'
  if (type === 'datetime') return '请选择时间'
  if (numericFieldTypes.has(type)) return '请输入数字'
  if (['select', 'cascade', 'radio', 'checkbox', 'member', 'department', 'company', 'location', 'related_approval', 'collection_account', 'external_contact', 'industry_department', 'province_city', 'invoice', 'customer', 'budget_request', 'related_contract', 'engineering_project', 'rating'].includes(type)) return '请选择'
  if (['attachment', 'image', 'signature'].includes(type)) return type === 'signature' ? '请签名' : '上传附件'
  return '请填写'
}

function fieldHint(field: any) {
  const type = controlType(field)
  if (type === 'static_text') return ''
  if (field.help_text) return field.help_text
  if (field.description) return field.description
  if (type === 'member') return employeeOptions.value.length ? '从组织架构中展开选择员工' : '暂无可选员工'
  if (type === 'department') return departmentOptions.value.length ? '从组织架构中展开选择部门' : '暂无可选部门'
  if (type === 'company') return companyOptions.value.length ? '请选择公司主体' : '暂无可选公司'
  if (type === 'duration') return `按${durationUnitLabel(field)}填写，提交时会换算进审批数据`
  if (type === 'location') return `定位范围：${locationRangeText(field)}`
  if (type === 'detail') return '可添加多条明细'
  return isRequired(field) ? '必填' : '选填'
}

function attachmentControlClass(field: any) {
  const type = controlType(field)
  return type === 'attachment'
    ? 'wecom-file-attachment-control'
    : 'wecom-attachment-control'
}

function compactFieldText(field: any) {
  return `${field?.code || ''} ${field?.label || ''}`.toLowerCase()
}

function isPunchCorrectionDateField(field: any) {
  if (!isPunchCorrectionSelected.value) return false
  return looksLikePunchCorrectionDateField(field)
}

function isPunchCorrectionSlotField(field: any) {
  if (!isPunchCorrectionSelected.value) return false
  return looksLikePunchCorrectionSlotField(field)
}

function isPunchCorrectionTimeField(field: any) {
  if (!isPunchCorrectionSelected.value) return false
  return looksLikePunchCorrectionTimeField(field)
}

function showPunchCorrectionMonthlyHint(field: any) {
  return isAttendanceAbnormalPunchCorrection.value && isPunchCorrectionDateField(field)
}

function punchCorrectionDateField() {
  return selectedTemplate.value ? templateFields(selectedTemplate.value).find(isPunchCorrectionDateField) : null
}

function punchCorrectionSlotField() {
  return selectedTemplate.value ? templateFields(selectedTemplate.value).find(isPunchCorrectionSlotField) : null
}

function punchCorrectionTimeField() {
  return selectedTemplate.value ? templateFields(selectedTemplate.value).find(isPunchCorrectionTimeField) : null
}

function punchCorrectionDateValue() {
  const field = punchCorrectionDateField()
  if (!field) return ''
  return normalizeDateTimeValue(formData.value[field.code], 'date')
}

function baseFieldOptions(field: any) {
  if (Array.isArray(field.options_json)) return field.options_json
  if (typeof field.options_json === 'string') {
    try {
      const parsed = JSON.parse(field.options_json)
      if (Array.isArray(parsed)) return parsed
      if (parsed && typeof parsed === 'object' && Array.isArray(parsed.options)) {
        return parsed.options.map((item: any) => typeof item === 'object' ? (item.label || item.value) : item).filter(Boolean)
      }
    } catch {
      return field.options_json.split(',').map((item: string) => item.trim()).filter(Boolean)
    }
  }
  if (field.options_json && typeof field.options_json === 'object' && Array.isArray(field.options_json.options)) {
    return field.options_json.options.map((item: any) => typeof item === 'object' ? (item.label || item.value) : item).filter(Boolean)
  }
  const rules = field.validation_rules || {}
  if (Array.isArray(rules.options)) return rules.options
  if (typeof rules.options === 'string') return rules.options.split(',').map((item: string) => item.trim()).filter(Boolean)
  if (controlType(field) === 'radio' || controlType(field) === 'checkbox' || controlType(field) === 'cascade') return ['选项1', '选项2']
  return []
}

function fieldOptions(field: any) {
  if (isPunchCorrectionSlotField(field)) {
    return punchCorrectionSlotOptionsForField(field)
      .filter((item) => item.canApply)
      .map((item) => item.value)
  }
  return baseFieldOptions(field)
}

function controlOption(field: any, key: string, fallback: any) {
  const options = field?.options_json
  if (options && typeof options === 'object' && options[key] !== undefined && options[key] !== null && options[key] !== '') {
    return options[key]
  }
  return fallback
}

function isLeaveTypeBalanceField(field: any) {
  if (!selectedTemplate.value || !isLeaveTemplate(selectedTemplate.value)) return false
  if (!sortedLeaveBalanceOptions.value.length) return false
  const type = controlType(field)
  if (!['select', 'cascade', 'radio'].includes(type)) return false
  const code = String(field?.code || '').toLowerCase()
  const label = String(field?.label || '')
  const component = String(controlOption(field, 'attendance_component', '') || '').toLowerCase()
  return code === 'leave_type' || label.includes('请假类型') || (component === 'leave' && code.includes('leave_type'))
}

function leaveBalanceOptionForValue(value: any) {
  const raw = String(value || '').trim()
  if (!raw) return null
  return sortedLeaveBalanceOptions.value.find((option) => (
    option.leave_type_name === raw
    || option.leave_type_code === raw
    || option.option_label === raw
    || String(option.leave_type_id) === raw
  )) || null
}

function leaveTypeDisplayValue(field: any) {
  const current = formData.value[field.code]
  const option = leaveBalanceOptionForValue(current)
  if (option) return option.option_label
  if (current) return String(current)
  if (leaveBalanceLoading.value) return '加载中'
  return fieldPlaceholder(field)
}

function leaveTypeHelpText(field: any) {
  const option = leaveBalanceOptionForValue(formData.value[field.code])
  if (!option) return ''
  if (!option.quota_limited && option.leave_type_code !== 'comp_time') {
    return `${option.leave_type_name}不限额。`
  }
  return `${option.leave_type_name}剩余${option.remaining_text || option.balance_text || '0天'}。`
}

function showLeaveBalanceLink(field: any) {
  return isLeaveTypeBalanceField(field) && !leaveBalanceError.value
}

function leaveTypeOptionCaption(option: LeaveBalanceOption) {
  if (option.reason) return option.reason
  if (!option.quota_limited && option.leave_type_code !== 'comp_time') return '不限额'
  return `已用 ${formatLeaveAmount(option.used_days, option)}`
}

function selectedLeaveBalanceOption() {
  if (!selectedTemplate.value || !isLeaveTemplate(selectedTemplate.value)) return null
  const leaveTypeField = templateFields(selectedTemplate.value).find((field: any) => isLeaveTypeBalanceField(field))
  return leaveTypeField ? leaveBalanceOptionForValue(formData.value[leaveTypeField.code]) : null
}

function durationLeaveBalanceOption(field: any) {
  if (!selectedTemplate.value || !isLeaveTemplate(selectedTemplate.value)) return null
  const component = String(controlOption(field, 'attendance_component', '') || '').toLowerCase()
  const text = `${field?.code || ''} ${field?.label || ''}`.toLowerCase()
  if (component && component !== 'leave') return null
  if (component !== 'leave' && !text.includes('leave') && !text.includes('请假')) return null
  return selectedLeaveBalanceOption()
}

function formatLeaveAmount(value: number, option: LeaveBalanceOption) {
  const numeric = Number(value || 0)
  if (option.leave_unit === 'hour') {
    return `${compactNumber(numeric * Number(option.hours_per_day || 8))}小时`
  }
  return `${compactNumber(numeric)}天`
}

function compactNumber(value: number) {
  const numeric = Number(value || 0)
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/\.?0+$/, '')
}

function normalizedControlOptions(field: any) {
  let options = field?.options_json
  if (typeof options === 'string') {
    try {
      options = JSON.parse(options)
    } catch {
      options = null
    }
  }
  return options && typeof options === 'object' && !Array.isArray(options) ? options : {}
}

function selectionMode(field: any) {
  return controlOption(field, 'selection_mode', controlOption(field, 'multiple', false) ? 'multiple' : 'single')
}

function defaultFormValue(field: any) {
  const type = controlType(field)
  const defaultValue = field.default_value
  if (type === 'checkbox') return Array.isArray(defaultValue) ? defaultValue : []
  if (type === 'date_range') return defaultValue && typeof defaultValue === 'object' ? defaultValue : { start: '', end: '' }
  if (type === 'duration') return defaultValue && typeof defaultValue === 'object' ? defaultValue : { start: '', end: '', value: '' }
  if (type === 'attachment' || type === 'image' || type === 'signature' || type === 'detail') return Array.isArray(defaultValue) ? defaultValue : []
  if (type === 'phone') return defaultValue && typeof defaultValue === 'object' ? defaultValue : { country_code: controlOption(field, 'country_code', '+86'), number: '' }
  if (type === 'collection_account') return defaultValue && typeof defaultValue === 'object' ? defaultValue : { account_name: '', bank_name: '', account_no: '' }
  if ((type === 'member' || type === 'department') && selectionMode(field) === 'multiple') return Array.isArray(defaultValue) ? defaultValue : []
  if (type === 'company') return defaultValue ?? ''
  if (defaultValue !== undefined && defaultValue !== null) return defaultValue
  return ''
}

function arrayFieldValue(field: any): any[] {
  const value = formData.value[field.code]
  return Array.isArray(value) ? value : []
}

function toggleArrayValue(field: any, value: any) {
  const current = new Set(arrayFieldValue(field))
  if (current.has(value)) current.delete(value)
  else current.add(value)
  formData.value[field.code] = Array.from(current)
}

function handleSelectFieldChange(field: any) {
  if (!isPunchCorrectionSlotField(field)) return
  const current = String(formData.value[field.code] || '')
  const slot = punchCorrectionSlotOptionsForField(field).find((item) => item.value === current) || null
  if (!slot || !slot.canApply) {
    const timeField = punchCorrectionTimeField()
    const firstAvailable = firstPunchCorrectionSlot()
    if (firstAvailable) {
      applyPunchCorrectionSlot(firstAvailable)
    } else {
      formData.value[field.code] = ''
      if (timeField) formData.value[timeField.code] = ''
    }
    return
  }
  applyPunchCorrectionSlot(slot)
}

function durationDateInputType(field: any) {
  return durationTimeScale(field) === 'hour' ? 'datetime-local' : 'date'
}

function dateTimePresentation(field: any): DateTimePresentation {
  return controlType(field) === 'datetime' ? 'date-time' : 'date'
}

function durationPickerPresentation(field: any): DateTimePresentation {
  return durationTimeScale(field) === 'hour' ? 'date-time' : 'date'
}

function durationPickerPlaceholder(field: any) {
  return durationPickerPresentation(field) === 'date-time' ? '请选择时间' : '请选择日期'
}

function numericControlOption(field: any, key: string, fallback: number) {
  const numeric = Number(controlOption(field, key, fallback))
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback
}

function durationTimeScale(field: any) {
  const leaveOption = durationLeaveBalanceOption(field)
  if (leaveOption?.leave_unit === 'hour') return 'hour'
  return String(controlOption(field, 'time_scale', 'day') || 'day')
}

function durationMode(field: any) {
  const leaveOption = durationLeaveBalanceOption(field)
  return String(leaveOption?.time_calc || controlOption(field, 'duration_mode', 'natural_day') || 'natural_day')
}

function durationHoursPerDay(field: any) {
  const leaveOption = durationLeaveBalanceOption(field)
  const candidate = Number(leaveOption?.hours_per_day || controlOption(field, 'unit_hours', 8) || 8)
  return Number.isFinite(candidate) && candidate > 0 ? candidate : 8
}

function currentPickerValue(presentation: DateTimePresentation) {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  if (presentation === 'date') return date
  return `${date}T${pad(now.getHours())}:${pad(now.getMinutes())}`
}

function normalizeDateTimeValue(value: unknown, presentation: DateTimePresentation) {
  const raw = Array.isArray(value) ? value[0] : value
  if (raw === undefined || raw === null) return ''
  const text = String(raw).trim()
  if (!text) return ''
  const dateMatch = text.match(/^(\d{4}-\d{2}-\d{2})/)
  const datePart = dateMatch?.[1] || text.slice(0, 10)
  if (presentation === 'date') return datePart
  const timeMatch = text.match(/[T\s](\d{2}:\d{2})/)
  const timePart = timeMatch?.[1] || '00:00'
  return `${datePart}T${timePart}`
}

function dateTimeDisplayValue(value: unknown, presentation: DateTimePresentation, placeholder: string) {
  const normalized = normalizeDateTimeValue(value, presentation)
  if (!normalized) return placeholder
  const [datePart, timePart = ''] = normalized.split('T')
  const displayDate = datePart.replace(/-/g, '/')
  if (presentation === 'date') return displayDate
  return `${displayDate} ${timePart.slice(0, 5)}`
}

function localIsoFromDatetimeLocal(value: unknown) {
  const normalized = normalizeDateTimeValue(value, 'date-time')
  if (!normalized) return ''
  const [datePart, timePart = '00:00'] = normalized.split('T')
  return `${datePart}T${timePart.slice(0, 5)}:00+08:00`
}

function normalizePunchDate(value: unknown) {
  return normalizeDateTimeValue(value, 'date')
}

function punchCorrectionTargetDay() {
  const data = punchEligibilityData.value
  if (!data) return null
  const targetDate = String(data.target_date || punchEligibilityDate.value || '')
  const days = Array.isArray(data.days) ? data.days : []
  return days.find((item: any) => String(item?.date || '') === targetDate) || null
}

function punchCorrectionRawSlots() {
  const targetDay = punchCorrectionTargetDay()
  if (!targetDay) return []
  const shifts = Array.isArray(targetDay.shifts) ? targetDay.shifts : []
  const slots: Array<Omit<PunchCorrectionSlotOption, 'value'>> = []
  shifts.forEach((shift: any, shiftIndex: number) => {
    const punches = Array.isArray(shift.patchable_punches) ? shift.patchable_punches : []
    punches
      .forEach((punch: any, punchIndex: number) => {
        const punchType = String(punch.punch_type || '') === 'check_out' ? 'check_out' : 'check_in'
        const shiftName = String(shift.shift_name || shift.segment_name || '默认班次')
        const punchLabel = String(punch.label || (punchType === 'check_out' ? '下班卡' : '上班卡'))
        const reason = String(punch.reason || '')
        const canApply = Boolean(punch.can_apply)
        slots.push({
          key: `${targetDay.date}-${shift.shift_id || shiftIndex}-${punchType}-${punchIndex}`,
          title: `${shiftName} · ${punchLabel}`,
          subtitle: reason,
          shift,
          punch,
          punchType,
          canApply,
          disabledReason: canApply ? '' : (reason || '当前班次不可补卡'),
        })
      })
  })
  return slots
}

function punchCorrectionSlotValue(field: any, slot: Omit<PunchCorrectionSlotOption, 'value'>) {
  if (slot.title) return slot.title
  const staticOptions = baseFieldOptions(field)
  const punchKeyword = slot.punchType === 'check_out' ? '下班' : '上班'
  const matched = staticOptions.find((option: any) => String(option).includes(punchKeyword))
  return matched ? String(matched) : slot.title
}

function punchCorrectionSlotOptionsForField(field: any): PunchCorrectionSlotOption[] {
  if (!field) return []
  return punchCorrectionRawSlots().map((slot) => ({
    ...slot,
    value: punchCorrectionSlotValue(field, slot),
  }))
}

function applyPunchCorrectionSlot(slot: PunchCorrectionSlotOption | null) {
  if (!slot || !slot.canApply) return
  const slotField = punchCorrectionSlotField()
  if (slotField) {
    formData.value[slotField.code] = slot.value
  }
  const timeField = punchCorrectionTimeField()
  if (timeField) {
    const allowed = slot.punch?.allowed_punch_time || {}
    const defaultTime = allowed.suggested || allowed.start || ''
    if (defaultTime) {
      formData.value[timeField.code] = normalizeDateTimeValue(defaultTime, 'date-time')
    }
  }
}

function selectPunchCorrectionSlot(slot: PunchCorrectionSlotOption) {
  if (!slot.canApply) {
    void presentToast(slot.disabledReason || '当前班次不可补卡', 'warning')
    return
  }
  applyPunchCorrectionSlot(slot)
}

function firstPunchCorrectionSlot() {
  const field = punchCorrectionSlotField()
  return field ? punchCorrectionSlotOptionsForField(field).find((item) => item.canApply) || null : null
}

function syncPunchCorrectionSelectionAfterEligibility() {
  const slotField = punchCorrectionSlotField()
  const timeField = punchCorrectionTimeField()
  if (slotField) {
    const options = punchCorrectionSlotOptionsForField(slotField)
    const current = String(formData.value[slotField.code] || '')
    const selected = options.find((item) => item.value === current)
    const firstAvailable = options.find((item) => item.canApply) || null
    if (firstAvailable && (!selected || !selected.canApply)) {
      applyPunchCorrectionSlot(firstAvailable)
    }
    if (!firstAvailable) {
      formData.value[slotField.code] = ''
    }
  }
  if (timeField && !firstPunchCorrectionSlot()) {
    formData.value[timeField.code] = ''
  }
}

function resetPunchCorrectionEligibility() {
  punchEligibility.value = null
  punchEligibilityError.value = ''
  punchEligibilityDate.value = ''
  const slotField = punchCorrectionSlotField()
  const timeField = punchCorrectionTimeField()
  if (slotField) formData.value[slotField.code] = ''
  if (timeField) formData.value[timeField.code] = ''
}

async function ensureAuthUser() {
  if (auth.user?.id) return true
  try {
    await auth.fetchUser()
  } catch {
    return false
  }
  return Boolean(auth.user?.id)
}

async function refreshPunchCorrectionEligibility(targetDate: string) {
  const normalizedDate = normalizePunchDate(targetDate)
  if (!normalizedDate || !selectedTemplate.value || !isPunchCorrectionTemplate(selectedTemplate.value)) return
  const hasUser = await ensureAuthUser()
  if (!hasUser || !auth.user?.id) {
    punchEligibility.value = null
    punchEligibilityError.value = '无法识别当前员工'
    await presentToast('无法识别当前员工，请重新登录', 'warning')
    return
  }
  punchEligibilityLoading.value = true
  punchEligibilityError.value = ''
  punchEligibilityDate.value = normalizedDate
  const requestDate = normalizedDate
  try {
    const result = await post<any>('/attendance/internal/punch-correction/eligibility', {
      employee_id: auth.user.id,
      target_date: normalizedDate,
      punch_types: ['check_in', 'check_out'],
      include_window_days: true,
      timezone: 'Asia/Shanghai',
      request_at: new Date().toISOString(),
      approval_context: {
        source: 'mobile_approval_start',
        approval_type_id: selectedTemplate.value?.id,
        approval_business_code: templateBusinessCode(selectedTemplate.value),
      },
    }, {
      headers: { 'X-Internal-Caller': 'approval' },
    })
    if (punchEligibilityDate.value !== requestDate) return
    punchEligibility.value = result
    punchEligibilityError.value = ''
    syncPunchCorrectionSelectionAfterEligibility()
    if (!result?.biz_success || !result?.data?.can_apply) {
      const message = result?.message || result?.data?.deny_reasons?.[0]?.message || '当前日期不可补卡'
      await presentToast(message, 'warning')
    }
  } catch (error: any) {
    if (punchEligibilityDate.value !== requestDate) return
    punchEligibility.value = null
    punchEligibilityError.value = responseErrorMessage(error)
    await presentToast(punchEligibilityError.value, 'danger')
  } finally {
    if (punchEligibilityDate.value === requestDate) {
      punchEligibilityLoading.value = false
    }
  }
}

function selectedPunchCorrectionTimeRange() {
  const slot = selectedPunchCorrectionSlot.value
  const allowed = slot?.punch?.allowed_punch_time || {}
  return {
    start: normalizeDateTimeValue(allowed.start, 'date-time'),
    end: normalizeDateTimeValue(allowed.end, 'date-time'),
    suggested: normalizeDateTimeValue(allowed.suggested, 'date-time'),
  }
}

function compareDateTimeText(a: string, b: string) {
  return a.localeCompare(b)
}

function punchCorrectionSelectedTimeValid() {
  const timeField = punchCorrectionTimeField()
  if (!timeField) return true
  const value = normalizeDateTimeValue(formData.value[timeField.code], 'date-time')
  const range = selectedPunchCorrectionTimeRange()
  if (!value || !range.start || !range.end) return false
  return compareDateTimeText(value, range.start) >= 0 && compareDateTimeText(value, range.end) <= 0
}

async function validatePunchCorrectionBeforeSubmit() {
  if (!isPunchCorrectionSelected.value) return true
  const dateValue = punchCorrectionDateValue()
  if (!dateValue) {
    await presentToast('请先选择补卡日期', 'warning')
    return false
  }
  if (!isAttendanceAbnormalPunchCorrection.value) {
    return true
  }
  if (!punchEligibility.value || punchEligibilityDate.value !== dateValue) {
    await refreshPunchCorrectionEligibility(dateValue)
  }
  if (!punchEligibility.value?.biz_success || !punchEligibility.value?.data?.can_apply) {
    await presentToast(punchEligibility.value?.message || punchEligibilityError.value || '当前日期不可补卡', 'warning')
    return false
  }
  if (!selectedPunchCorrectionSlot.value || !selectedPunchCorrectionSlot.value.canApply) {
    await presentToast('请先选择补卡班次', 'warning')
    return false
  }
  if (!punchCorrectionSelectedTimeValid()) {
    await presentToast('补卡时间不在允许范围内', 'warning')
    return false
  }
  return true
}

function parsePickerDate(value: unknown, presentation: DateTimePresentation) {
  const normalized = normalizeDateTimeValue(value, presentation)
  if (!normalized) return null
  const [datePart, timePart = '00:00'] = normalized.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  if (!year || !month || !day) return null
  const [hour = 0, minute = 0] = timePart.split(':').map(Number)
  const parsed = new Date(year, month - 1, day, hour || 0, minute || 0)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function isWeekday(date: Date) {
  const day = date.getDay()
  return day >= 1 && day <= 5
}

function inclusiveDayCount(start: Date, end: Date, workdayOnly: boolean) {
  const current = startOfLocalDay(start)
  const last = startOfLocalDay(end)
  if (last.getTime() < current.getTime()) return 0
  let count = 0
  while (current.getTime() <= last.getTime()) {
    if (!workdayOnly || isWeekday(current)) count += 1
    current.setDate(current.getDate() + 1)
  }
  return count
}

function sameLocalDay(left: Date, right: Date) {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate()
}

function nextLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1)
}

function workHourDuration(field: any, start: Date, end: Date) {
  const rawHours = (end.getTime() - start.getTime()) / 3600000
  if (rawHours <= 0) return 0
  const hoursPerDay = durationHoursPerDay(field)
  const workdayOnly = durationMode(field) === 'workday'
  if (sameLocalDay(start, end)) {
    if (workdayOnly && !isWeekday(start)) return 0
    return Math.min(rawHours, hoursPerDay)
  }

  let total = 0
  let current = startOfLocalDay(start)
  const last = startOfLocalDay(end)
  while (current.getTime() <= last.getTime()) {
    if (!workdayOnly || isWeekday(current)) {
      const segmentStart = current.getTime() === startOfLocalDay(start).getTime() ? start : current
      const segmentEnd = current.getTime() === last.getTime() ? end : nextLocalDay(current)
      const segmentHours = Math.max(0, (segmentEnd.getTime() - segmentStart.getTime()) / 3600000)
      total += Math.min(segmentHours, hoursPerDay)
    }
    current.setDate(current.getDate() + 1)
  }
  return total
}

function formatDurationNumber(value: number) {
  if (!Number.isFinite(value) || value <= 0) return ''
  const rounded = Math.round(value * 100) / 100
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2).replace(/\.?0+$/, '')
}

function calculatedDurationValue(field: any, rawValue: any) {
  const presentation = durationPickerPresentation(field)
  const start = parsePickerDate(rawValue?.start, presentation)
  const end = parsePickerDate(rawValue?.end, presentation)
  if (!start || !end || end.getTime() < start.getTime()) return ''

  if (durationTimeScale(field) === 'hour') {
    return formatDurationNumber(workHourDuration(field, start, end))
  }

  if (presentation === 'date') {
    const workdayOnly = durationMode(field) === 'workday'
    return formatDurationNumber(inclusiveDayCount(start, end, workdayOnly))
  }

  const unitHours = numericControlOption(field, 'unit_hours', 24)
  return formatDurationNumber((end.getTime() - start.getTime()) / 3600000 / unitHours)
}

function updateDurationFieldValue(field: any) {
  const current = formData.value[field.code]
  if (!current || typeof current !== 'object') return
  current.value = calculatedDurationValue(field, current)
}

function updateAllDurationFieldValues() {
  if (!selectedTemplate.value) return
  templateFields(selectedTemplate.value)
    .filter((field: any) => controlType(field) === 'duration')
    .forEach((field: any) => updateDurationFieldValue(field))
}

function durationDisplayValue(field: any) {
  const current = formData.value[field.code]
  if (!current || typeof current !== 'object') return ''
  return calculatedDurationValue(field, current) || String(current.value || '')
}

function durationAutoHint(field: any) {
  return `当前${field?.label || '时长'}为自动计算`
}

async function showDurationDetail(field: any) {
  const current = formData.value[field.code]
  const value = durationDisplayValue(field)
  if (!current?.start || !current?.end || !value) {
    await presentToast('请选择开始时间和结束时间', 'warning')
    return
  }
  const presentation = durationPickerPresentation(field)
  const startText = dateTimeDisplayValue(current.start, presentation, '')
  const endText = dateTimeDisplayValue(current.end, presentation, '')
  await presentToast(`${startText} 至 ${endText}，共 ${value}${durationUnitLabel(field)}`)
}

function openDateTimePicker(
  field: any,
  title: string,
  valueKey: '' | 'start' | 'end' = '',
  presentation: DateTimePresentation = dateTimePresentation(field),
) {
  if (isPunchCorrectionTimeField(field) && isAttendanceAbnormalPunchCorrection.value && !selectedPunchCorrectionSlot.value) {
    void presentToast('请先选择可补卡班次', 'warning')
    return
  }
  const currentValue = valueKey ? formData.value[field.code]?.[valueKey] : formData.value[field.code]
  const punchTimeRange = isPunchCorrectionTimeField(field) ? selectedPunchCorrectionTimeRange() : null
  dateTimePicker.fieldCode = field.code
  dateTimePicker.valueKey = valueKey
  dateTimePicker.title = title
  dateTimePicker.presentation = presentation
  dateTimePicker.min = punchTimeRange?.start || ''
  dateTimePicker.max = punchTimeRange?.end || ''
  dateTimePicker.draftValue = normalizeDateTimeValue(currentValue, presentation) || punchTimeRange?.suggested || punchTimeRange?.start || currentPickerValue(presentation)
  dateTimePicker.visible = true
}

function handleDateTimeDraftChange(event: CustomEvent<{ value?: string | string[] | null }>) {
  const normalized = normalizeDateTimeValue(event.detail.value, dateTimePicker.presentation)
  if (normalized) {
    dateTimePicker.draftValue = normalized
  }
}

function resetDateTimePicker() {
  dateTimePicker.visible = false
  dateTimePicker.title = ''
  dateTimePicker.fieldCode = ''
  dateTimePicker.valueKey = ''
  dateTimePicker.presentation = 'date'
  dateTimePicker.draftValue = ''
  dateTimePicker.min = ''
  dateTimePicker.max = ''
}

function closeDateTimePicker() {
  resetDateTimePicker()
}

function cancelDateTimePicker() {
  dateTimePicker.visible = false
}

async function confirmDateTimePicker() {
  const value = normalizeDateTimeValue(dateTimePicker.draftValue, dateTimePicker.presentation)
  const fieldCode = dateTimePicker.fieldCode
  const field = selectedTemplate.value
    ? templateFields(selectedTemplate.value).find((item: any) => item.code === fieldCode)
    : null
  if (dateTimePicker.fieldCode) {
    if (dateTimePicker.valueKey) {
      const current = formData.value[dateTimePicker.fieldCode]
      if (!current || typeof current !== 'object') {
        formData.value[dateTimePicker.fieldCode] = { start: '', end: '', value: '' }
      }
      formData.value[dateTimePicker.fieldCode][dateTimePicker.valueKey] = value
      if (field && controlType(field) === 'duration') {
        updateDurationFieldValue(field)
      }
    } else {
      formData.value[dateTimePicker.fieldCode] = value
    }
  }
  dateTimePicker.visible = false
  if (field && isAttendanceAbnormalPunchCorrection.value && isPunchCorrectionDateField(field)) {
    resetPunchCorrectionEligibility()
    formData.value[field.code] = value
    await refreshPunchCorrectionEligibility(value)
  }
  if (field && isAttendanceAbnormalPunchCorrection.value && isPunchCorrectionTimeField(field) && !punchCorrectionSelectedTimeValid()) {
    await presentToast('补卡时间不在允许范围内', 'warning')
  }
}

function durationUnitLabel(field: any) {
  return durationTimeScale(field) === 'hour' ? '小时' : '天'
}

function locationRangeText(field: any) {
  return `${controlOption(field, 'range_meters', 300)}米`
}

function setLocationValue(field: any) {
  formData.value[field.code] = `当前位置（${locationRangeText(field)}内）`
}

function normalizeAttachmentItem(value: any): ApprovalAttachment | null {
  if (!value) return null
  if (typeof value === 'string') {
    const url = value.trim()
    if (!url) return null
    return {
      name: fileNameFromUrl(url) || url,
      url,
    }
  }
  if (typeof value !== 'object' || Array.isArray(value)) return null
  const url = String(value.url || value.path || value.file_url || '').trim()
  if (!url) return null
  const name = String(value.name || value.filename || value.original_filename || '').trim()
  const size = Number(value.size)
  return {
    name: name || fileNameFromUrl(url) || '附件',
    filename: String(value.filename || name || '').trim() || undefined,
    url,
    size: Number.isFinite(size) && size >= 0 ? size : undefined,
    content_type: String(value.content_type || value.type || '').trim() || undefined,
    uploaded_at: String(value.uploaded_at || '').trim() || undefined,
  }
}

function attachmentItems(field: any): ApprovalAttachment[] {
  const value = formData.value[field.code]
  if (!Array.isArray(value)) return []
  return value.map(normalizeAttachmentItem).filter(Boolean) as ApprovalAttachment[]
}

function attachmentKey(item: ApprovalAttachment, index: number) {
  return `${item.url || item.name || 'attachment'}-${index}`
}

function fileNameFromUrl(url?: string | null) {
  if (!url) return ''
  const pathname = String(url).split('?')[0].split('#')[0]
  const name = pathname.split('/').filter(Boolean).pop() || ''
  try {
    return decodeURIComponent(name)
  } catch {
    return name
  }
}

function attachmentDisplayName(item: ApprovalAttachment, index: number) {
  return item.name || item.filename || fileNameFromUrl(item.url) || `附件${index + 1}`
}

function attachmentSizeText(size?: number) {
  if (!Number.isFinite(Number(size)) || Number(size) <= 0) return ''
  const bytes = Number(size)
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)}KB`
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)}MB`
}

function isAttachmentUploading(field: any) {
  return attachmentUploadingFieldCode.value === field.code
}

function pickAttachment(field: any) {
  const imageLike = controlType(field) === 'image' || controlType(field) === 'signature'
  const photoOnly = imageLike || Boolean(controlOption(field, 'mobile_photo_only', false))
  attachmentPicker.fieldCode = field.code
  attachmentPicker.accept = photoOnly ? 'image/*' : genericAttachmentAccept
  attachmentPicker.multiple = !photoOnly
  attachmentPicker.capture = photoOnly ? 'environment' : ''
  if (attachmentFileInput.value) {
    attachmentFileInput.value.value = ''
    attachmentFileInput.value.click()
  }
}

function removeAttachment(field: any, index: number) {
  const items = attachmentItems(field)
  items.splice(index, 1)
  formData.value[field.code] = items
}

function openAttachment(url?: string | null) {
  const resolved = resolveAuthenticatedAssetUrl(url)
  if (!resolved) return
  window.open(resolved, '_blank', 'noopener,noreferrer')
}

function uploadErrorMessage(error: any) {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return detail.map((item) => item?.msg || item?.message || String(item)).join('; ')
  if (detail) return String(detail)
  return '附件上传失败，请稍后重试'
}

async function handleAttachmentFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const fieldCode = attachmentPicker.fieldCode
  const files = Array.from(input.files || [])
  if (!fieldCode || !files.length) return
  attachmentUploadingFieldCode.value = fieldCode
  try {
    const uploaded: ApprovalAttachment[] = []
    for (const file of files) {
      const body = new FormData()
      body.append('file', file)
      const result = await post<{ url: string; filename: string; size: number }>(
        '/upload?category=approval',
        body,
      )
      uploaded.push({
        name: result.filename || file.name,
        filename: result.filename || file.name,
        url: result.url,
        size: result.size || file.size,
        content_type: file.type || undefined,
        uploaded_at: new Date().toISOString(),
      })
    }
    formData.value[fieldCode] = [...attachmentItems({ code: fieldCode }), ...uploaded]
    await presentToast(uploaded.length > 1 ? `已上传 ${uploaded.length} 个附件` : '附件上传成功')
  } catch (error: any) {
    await presentToast(uploadErrorMessage(error), 'danger')
  } finally {
    attachmentUploadingFieldCode.value = ''
    input.value = ''
  }
}

function selectRelatedApproval(field: any) {
  formData.value[field.code] = '关联申请单'
}

function detailRows(field: any) {
  if (!Array.isArray(formData.value[field.code])) {
    formData.value[field.code] = []
  }
  return formData.value[field.code]
}

function detailRowsForDisplay(field: any) {
  const rows = detailRows(field)
  if (!rows.length) {
    rows.push(defaultDetailRow(field))
  }
  return rows
}

function detailChildFields(field: any) {
  const options = normalizedControlOptions(field)
  const children = options?.child_fields || options?.fields || options?.children
  return Array.isArray(children)
    ? children.filter((child: any) => controlType(child) !== 'detail')
    : []
}

function detailSummaryConfig(field: any) {
  const options = normalizedControlOptions(field)
  const summary = options.summary || options.auto_summary
  return summary && typeof summary === 'object' && !Array.isArray(summary) ? summary : null
}

function detailSummaryEnabled(field: any) {
  return detailSummaryConfig(field)?.enabled === true
}

function detailSummaryLabel(field: any) {
  const label = String(detailSummaryConfig(field)?.label || '').trim()
  return label || `${field?.label || '明细'}合计`
}

function numericDetailChildFields(field: any) {
  return detailChildFields(field).filter((child: any) => detailSummaryFieldTypes.has(controlType(child)))
}

function detailSummaryFields(field: any) {
  const numericChildren = numericDetailChildFields(field)
  const configured = detailSummaryConfig(field)?.field_codes
  const configuredCodes = Array.isArray(configured)
    ? configured.map((code: any) => String(code || '').trim()).filter(Boolean)
    : []
  if (!configuredCodes.length) return numericChildren
  return numericChildren.filter((child: any) => configuredCodes.includes(child.code))
}

function numericInputValue(value: unknown) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0
  const normalized = String(value ?? '').replace(/,/g, '').trim()
  if (!normalized) return 0
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : 0
}

function detailSummaryTotal(field: any) {
  const rows = detailRows(field)
  const fields = detailSummaryFields(field)
  return rows.reduce((total: number, row: Record<string, unknown>) => {
    if (!fields.length && Object.prototype.hasOwnProperty.call(row, 'amount')) {
      return total + numericInputValue(row.amount)
    }
    return total + fields.reduce((rowTotal: number, child: any) => rowTotal + numericInputValue(row[child.code]), 0)
  }, 0)
}

function formatDetailSummaryTotal(field: any) {
  const total = detailSummaryTotal(field)
  if (Number.isInteger(total)) return String(total)
  return total.toFixed(2).replace(/\.?0+$/, '')
}

function detailSummaryDisplayValue(field: any) {
  return detailSummaryTotal(field) ? formatDetailSummaryTotal(field) : '自动计算结果'
}

function detailChildUsesOptions(field: any) {
  const type = controlType(field)
  return ['select', 'cascade', 'radio'].includes(type) && fieldOptions(field).length > 0
}

function detailChildInputType(field: any): 'text' | 'number' | 'date' | 'datetime-local' {
  const type = controlType(field)
  if (numericFieldTypes.has(type)) return 'number'
  if (type === 'date') return 'date'
  if (type === 'datetime') return 'datetime-local'
  return 'text'
}

function detailChildInputMode(field: any) {
  return numericFieldTypes.has(controlType(field)) ? 'decimal' : undefined
}

function defaultDetailRow(field: any) {
  const row: Record<string, any> = {
    id: `${Date.now()}_${Math.random().toString(16).slice(2, 6)}`,
  }
  const children = detailChildFields(field)
  if (!children.length) {
    row.title = ''
    row.amount = ''
    return row
  }
  for (const child of children) {
    row[child.code] = ''
  }
  return row
}

function addDetailRow(field: any) {
  detailRows(field).push(defaultDetailRow(field))
}

function copyDetailRow(field: any) {
  const rows = detailRowsForDisplay(field)
  const source = rows[0] || defaultDetailRow(field)
  detailRows(field).push({
    ...JSON.parse(JSON.stringify(source)),
    id: `${Date.now()}_${Math.random().toString(16).slice(2, 6)}`,
  })
}

function isRequired(field: any) {
  const required = field.required === true || field.is_required === true
  if (isPunchCorrectionSlotField(field)) {
    return shouldShowPunchCorrectionSlotField(field) && (required || isAttendanceAbnormalPunchCorrection.value)
  }
  return required
}

function hasValue(value: any): boolean {
  if (Array.isArray(value)) return value.length > 0
  if (value && typeof value === 'object') {
    if ('number' in value || 'country_code' in value) return Boolean(String(value.number || '').trim())
    if ('value' in value && ('start' in value || 'end' in value)) return Boolean(value.start && value.end && String(value.value || '').trim())
    if ('value' in value) return Boolean(String(value.value || '').trim())
    if ('start' in value || 'end' in value) return Boolean(value.start && value.end)
    if ('account_no' in value) return Boolean(String(value.account_no || '').trim())
    return Object.values(value).some((item) => hasValue(item))
  }
  return value !== undefined && value !== null && String(value).trim() !== ''
}

function normalizedFormData() {
  const next: Record<string, any> = {}
  const fields = selectedTemplate.value ? templateFields(selectedTemplate.value) : []
  for (const field of fields) {
    const code = field.code
    const rawValue = formData.value[code]
    const fieldType = String(field.field_type || '').toLowerCase()
    if (fieldType === 'static_text' || fieldType === 'layout_column') {
      continue
    }
    if (fieldType === 'duration') {
      const calculated = calculatedDurationValue(field, rawValue)
      const durationValue = calculated || (rawValue?.value ?? rawValue?.duration ?? '')
      const normalized = Number(durationValue)
      next[code] = Number.isNaN(normalized) ? durationValue : normalized
      if (!hasValue(next.duration)) next.duration = next[code]
      next[`${code}_unit`] = durationUnitLabel(field)
      next[`${code}_time_scale`] = durationTimeScale(field)
      if (rawValue?.start) next[`${code}_start_time`] = rawValue.start
      if (rawValue?.end) next[`${code}_end_time`] = rawValue.end
    } else if (isLeaveTypeBalanceField(field) && hasValue(rawValue)) {
      const option = leaveBalanceOptionForValue(rawValue)
      next[code] = option?.leave_type_name || rawValue
      if (option) {
        next.leave_type_id = Number(option.leave_type_id)
        next.leave_type_code = option.leave_type_code || ''
        next.leave_type_name = option.leave_type_name
        next.leave_type_label = option.option_label
        next.leave_balance_snapshot = {
          year: new Date().getFullYear(),
          remaining_days: option.remaining_days,
          remaining_text: option.remaining_text,
          balance_text: option.balance_text,
        }
      }
    } else if (fieldType === 'phone') {
      next[code] = `${rawValue?.country_code || '+86'} ${rawValue?.number || ''}`.trim()
    } else if (fieldType === 'detail') {
      const rows = Array.isArray(rawValue) ? rawValue : []
      if (detailSummaryEnabled(field)) {
        next[code] = {
          rows,
          summary: {
            enabled: true,
            label: detailSummaryLabel(field),
            total: detailSummaryTotal(field),
            field_codes: detailSummaryFields(field).map((child: any) => child.code),
          },
        }
      } else {
        next[code] = rows
      }
    } else if (['attachment', 'image', 'signature'].includes(fieldType)) {
      next[code] = attachmentItems(field).map((item) => ({
        name: item.name || item.filename || fileNameFromUrl(item.url),
        filename: item.filename || item.name || fileNameFromUrl(item.url),
        url: item.url,
        size: item.size,
        content_type: item.content_type,
        uploaded_at: item.uploaded_at,
      }))
    } else if (['member', 'department'].includes(fieldType) && Array.isArray(rawValue)) {
      next[code] = rawValue.map((item) => {
        const normalized = Number(item)
        return Number.isNaN(normalized) ? item : normalized
      })
    } else if ((numericFieldTypes.has(fieldType) || fieldType === 'member' || fieldType === 'department' || fieldType === 'company') && hasValue(rawValue)) {
      const normalized = Number(rawValue)
      next[code] = Number.isNaN(normalized) ? rawValue : normalized
    } else {
      next[code] = rawValue
    }
  }
  for (const dynamicCode of ['selected_approver_ids', 'approval_selected_approver_ids', 'cc_ids', 'selected_cc_ids']) {
    const value = formData.value[dynamicCode]
    if (Array.isArray(value) && value.length) {
      next[dynamicCode] = value.map((item) => {
        const normalized = Number(item)
        return Number.isNaN(normalized) ? item : normalized
      })
    } else if (hasValue(value)) {
      const normalized = Number(value)
      next[dynamicCode] = Number.isNaN(normalized) ? value : normalized
    }
  }
  if (selectedTemplate.value && isPunchCorrectionTemplate(selectedTemplate.value)) {
    const dateValue = punchCorrectionDateValue()
    const timeField = punchCorrectionTimeField()
    const timeValue = timeField ? formData.value[timeField.code] : ''
    const slot = selectedPunchCorrectionSlot.value
    next.employee_id = auth.user?.id
    next.correction_date = dateValue
    next.target_date = dateValue
    next.punch_time = localIsoFromDatetimeLocal(timeValue)
    next.punch_type = slot?.punchType || next.punch_type
    next.punch_type_label = slot?.punch?.label || ''
    next.shift_id = slot?.shift?.shift_id || null
    next.shift_name = slot?.shift?.shift_name || slot?.shift?.segment_name || ''
    next.patch_reason_code = slot?.punch?.reason_code || ''
    next.patch_reason = slot?.punch?.reason || ''
    next.allowed_punch_time = slot?.punch?.allowed_punch_time || null
    next.normal_result_time = slot?.punch?.normal_result_time || null
    next.eligibility_biz_code = punchEligibility.value?.biz_code || ''
  }
  return next
}

function normalizeList(res: any): any[] {
  return res?.items || res?.data || res || []
}

function normalizeEmployeeOptions(res: any): any[] {
  return normalizeList(res).map((employee: any) => ({
    ...employee,
    id: Number(employee.id),
    department_id: employee.department_id == null ? null : Number(employee.department_id),
    role_names: Array.isArray(employee.role_names || employee.roles)
      ? Array.from(new Set((employee.role_names || employee.roles).map((role: unknown) => String(role || '').trim().toLowerCase()).filter(Boolean)))
      : [],
  }))
}

function buildDepartmentTree(list: any[]) {
  const map = new Map<number, any>()
  for (const department of list || []) {
    map.set(Number(department.id), { ...department, children: [] })
  }

  const roots: any[] = []
  for (const department of list || []) {
    const id = Number(department.id)
    const parentId = Number(department.parent_id || 0)
    const node = map.get(id)
    if (parentId && map.has(parentId)) {
      map.get(parentId).children.push(node)
    } else {
      roots.push(node)
    }
  }

  const sortNodes = (nodes: any[]) => {
    nodes.sort((a, b) => {
      const order = Number(a.sort_order || 0) - Number(b.sort_order || 0)
      if (order !== 0) return order
      return String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN')
    })
    nodes.forEach((node) => sortNodes(node.children || []))
  }
  sortNodes(roots)
  return roots
}

function companyLabel(company: any) {
  return String(company?.short_name || company?.name || `公司 ${company?.id || ''}`).trim()
}

function companyDisplayValue(field: any) {
  const rawValue = formData.value[field.code]
  const value = Number(rawValue)
  if (!value) return field.placeholder || '请选择'
  const company = companyOptions.value.find((item) => Number(item.id) === value)
  return company ? companyLabel(company) : field.placeholder || '请选择'
}

async function ensureCompanyOptionsLoaded() {
  if (companyOptions.value.length) return
  try {
    const res = await get('/payroll/companies', { active_only: true })
    companyOptions.value = normalizeList(res)
  } catch {
    companyOptions.value = []
  }
}

async function openCompanyPicker(field: any) {
  await ensureCompanyOptionsLoaded()
  companyPicker.fieldCode = field.code
  companyPicker.title = field.label || '所在公司'
  const current = Number(formData.value[field.code])
  companyPicker.draftValue = Number.isFinite(current) && current > 0 ? current : null
  companyPicker.visible = true
}

function cancelCompanyPicker() {
  companyPicker.visible = false
}

function confirmCompanyPicker() {
  if (companyPicker.fieldCode && companyPicker.draftValue) {
    formData.value[companyPicker.fieldCode] = companyPicker.draftValue
  }
  companyPicker.visible = false
}

async function openLeaveTypePicker(field: any) {
  if (!leaveBalanceOptions.value.length && !leaveBalanceLoading.value) {
    await loadLeaveBalanceOptionsForTemplate()
  }
  leaveTypePicker.fieldCode = field.code
  leaveTypePicker.title = field.label || '请假类型'
  const current = leaveBalanceOptionForValue(formData.value[field.code])
  leaveTypePicker.draftValue = current?.leave_type_name || ''
  leaveTypePicker.visible = true
}

function cancelLeaveTypePicker() {
  leaveTypePicker.visible = false
}

function confirmLeaveTypePicker() {
  if (leaveTypePicker.fieldCode && leaveTypePicker.draftValue) {
    formData.value[leaveTypePicker.fieldCode] = leaveTypePicker.draftValue
    updateAllDurationFieldValues()
  }
  leaveTypePicker.visible = false
}

function openOrgPicker(field: any) {
  const kind = controlType(field) as OrgPickerKind
  orgPicker.kind = kind
  orgPicker.fieldCode = field.code
  orgPicker.title = field.label || field.code
  orgPicker.multiple = selectionMode(field) === 'multiple'
  orgPicker.allowedMemberIds = []
  orgPicker.restrictMembers = false
  if (orgPicker.multiple && !Array.isArray(formData.value[field.code])) {
    formData.value[field.code] = []
  }
  orgPicker.visible = true
  const next = new Set<string>()
  for (const department of departmentTree.value) {
    next.add(`department-${department.id}`)
  }
  expandedOrgKeys.value = next
}

function closeOrgPicker() {
  orgPicker.visible = false
  orgPicker.allowedMemberIds = []
  orgPicker.restrictMembers = false
}

function toggleOrgRow(row: OrgPickerRow) {
  if (row.kind !== 'department' || !row.expandable) return
  const next = new Set(expandedOrgKeys.value)
  if (next.has(row.key)) next.delete(row.key)
  else next.add(row.key)
  expandedOrgKeys.value = next
}

function handleOrgRowClick(row: OrgPickerRow) {
  if (orgPicker.kind === 'member') {
    if (row.kind === 'department') {
      toggleOrgRow(row)
      return
    }
    if (orgPicker.multiple) {
      const current = new Set(arrayFieldValue({ code: orgPicker.fieldCode }))
      if (current.has(row.id)) current.delete(row.id)
      else current.add(row.id)
      formData.value[orgPicker.fieldCode] = Array.from(current)
      return
    }
    formData.value[orgPicker.fieldCode] = row.id
    closeOrgPicker()
    return
  }

  if (row.kind === 'department') {
    if (orgPicker.multiple) {
      const current = new Set(arrayFieldValue({ code: orgPicker.fieldCode }))
      if (current.has(row.id)) current.delete(row.id)
      else current.add(row.id)
      formData.value[orgPicker.fieldCode] = Array.from(current)
      return
    }
    formData.value[orgPicker.fieldCode] = row.id
    closeOrgPicker()
  }
}

function isOrgRowSelected(row: OrgPickerRow) {
  if (orgPicker.multiple) {
    return arrayFieldValue({ code: orgPicker.fieldCode }).map(Number).includes(row.id)
  }
  const current = Number(formData.value[orgPicker.fieldCode])
  if (!current) return false
  return orgPicker.kind === 'member'
    ? row.kind === 'employee' && row.id === current
    : row.kind === 'department' && row.id === current
}

function organizationDisplayValue(field: any) {
  const type = controlType(field)
  const rawValue = formData.value[field.code]
  if (Array.isArray(rawValue)) {
    if (!rawValue.length) return field.placeholder || (type === 'member' ? '请选择员工' : '请选择部门')
    const labels = rawValue.map((id) => {
      const value = Number(id)
      if (type === 'member') {
        const employee = employeeOptions.value.find((item) => Number(item.id) === value)
        return employee?.name || `员工 ${value}`
      }
      return departmentNameById.value.get(value) || `部门 ${value}`
    })
    return labels.join('、')
  }
  const value = Number(rawValue)
  if (!value) return field.placeholder || (type === 'member' ? '请选择员工' : '请选择部门')
  if (type === 'member') {
    const employee = employeeOptions.value.find((item) => Number(item.id) === value)
    if (!employee) return field.placeholder || '请选择员工'
    const deptName = employee.department_name || departmentNameById.value.get(Number(employee.department_id)) || '未分配部门'
    return `${employee.name || employee.employee_no || employee.id} · ${deptName}`
  }
  return departmentNameById.value.get(value) || field.placeholder || '请选择部门'
}

function isNotifyPreviewNode(node: any) {
  const text = `${node?.node_name || ''} ${node?.node_type || ''} ${node?.node_type_label || ''}`.toLowerCase()
  return text.includes('抄送') || text.includes('notify') || text.includes('cc') || text.includes('copy')
}

function isCurrentUserFlowPerson(person: string) {
  const text = String(person || '').trim()
  if (!text) return false
  if (['发起人', '申请人', '本人', '我自己', '自己'].includes(text)) return true
  const userName = String(auth.user?.name || '').trim()
  return Boolean(userName && text === userName)
}

function visibleFlowNodePeople(node: any, people: string[]) {
  if (!isNotifyPreviewNode(node)) return people
  return people.filter((person) => !isCurrentUserFlowPerson(person))
}

function shouldDisplayFlowPreviewNode(node: any) {
  if (!isNotifyPreviewNode(node)) return true
  return flowNodePeople(node).length > 0
}

function flowSelectionFieldCode(node: any) {
  if (node?.requires_applicant_select || node?.assignee_source === 'applicant_select' || node?.approver_type === 'applicant_select') {
    return 'selected_approver_ids'
  }
  if (isNotifyPreviewNode(node) && node?.allow_applicant_select !== false) {
    return 'cc_ids'
  }
  return ''
}

function canAppendFlowPerson(node: any) {
  return Boolean(flowSelectionFieldCode(node))
}

function flowAppendButtonLabel(node: any) {
  return flowSelectionFieldCode(node) === 'cc_ids' ? '添加抄送人' : '选择审批人'
}

function selectedFlowPeople(node: any) {
  const fieldCode = flowSelectionFieldCode(node)
  if (!fieldCode) return []
  const rawValue = formData.value[fieldCode]
  const selectedValues = Array.isArray(rawValue) ? rawValue : (hasValue(rawValue) ? [rawValue] : [])
  return selectedValues.map((id) => {
    const memberId = Number(id)
    const employee = employeeOptions.value.find((item) => Number(item.id) === memberId)
    return employee?.name || `员工 ${memberId}`
  })
}

function applicantSelectAllowedMemberIds(node: any) {
  if (!node || flowSelectionFieldCode(node) !== 'selected_approver_ids') return null
  const scope = String(node.applicant_select_scope || 'company')
  if (scope === 'selected_members') {
    return normalizeList(node.applicant_select_member_ids).map(Number).filter(Boolean)
  }
  if (scope === 'role') {
    const roles = new Set(normalizeList(node.applicant_select_roles).map((role) => String(role || '').trim().toLowerCase()).filter(Boolean))
    if (!roles.size) return []
    return employeeOptions.value
      .filter((employee) => Array.isArray(employee.role_names) && employee.role_names.some((role: string) => roles.has(String(role || '').toLowerCase())))
      .map((employee) => Number(employee.id))
      .filter(Boolean)
  }
  return null
}

function pruneFlowSelectedPeople(fieldCode: string, allowedMemberIds: number[] | null) {
  if (!fieldCode || allowedMemberIds === null) return
  const allowed = new Set(allowedMemberIds.map(Number))
  if (Array.isArray(formData.value[fieldCode])) {
    formData.value[fieldCode] = formData.value[fieldCode].filter((id: unknown) => allowed.has(Number(id)))
    return
  }
  if (hasValue(formData.value[fieldCode]) && !allowed.has(Number(formData.value[fieldCode]))) {
    formData.value[fieldCode] = ''
  }
}

async function ensureFlowMemberPickerOptions() {
  const tasks: Promise<void>[] = []
  if (departmentOptions.value.length === 0) {
    tasks.push(get('/departments').then((res) => { departmentOptions.value = normalizeList(res) }).catch(() => { departmentOptions.value = [] }))
  }
  if (employeeOptions.value.length === 0) {
    tasks.push(get('/employees/selector', { limit: 1000 }).then((res) => { employeeOptions.value = normalizeEmployeeOptions(res) }).catch(() => { employeeOptions.value = [] }))
  }
  if (tasks.length) await Promise.all(tasks)
}

async function openFlowPersonPicker(node: any) {
  const fieldCode = flowSelectionFieldCode(node)
  if (!fieldCode) return
  await ensureFlowMemberPickerOptions()
  const allowedMemberIds = applicantSelectAllowedMemberIds(node)
  pruneFlowSelectedPeople(fieldCode, allowedMemberIds)
  const shouldSelectMultiple = fieldCode === 'cc_ids' || node?.applicant_select_mode !== 'single'
  if (shouldSelectMultiple && !Array.isArray(formData.value[fieldCode])) {
    formData.value[fieldCode] = []
  }
  orgPicker.kind = 'member'
  orgPicker.fieldCode = fieldCode
  orgPicker.title = flowAppendButtonLabel(node)
  orgPicker.multiple = shouldSelectMultiple
  orgPicker.allowedMemberIds = allowedMemberIds || []
  orgPicker.restrictMembers = allowedMemberIds !== null
  orgPicker.visible = true
  const next = new Set<string>()
  for (const department of departmentTree.value) {
    next.add(`department-${department.id}`)
  }
  expandedOrgKeys.value = next
}

function flowNodeTitle(node: any) {
  return node?.node_name || node?.node_type_label || node?.node_type || '审批人'
}

function flowNodeIcon(node: any) {
  if (isNotifyPreviewNode(node)) return '↗'
  return node?.node_order ? String(node.node_order) : '人'
}

function flowNodePeople(node: any) {
  const selected = selectedFlowPeople(node)
  if (selected.length) return visibleFlowNodePeople(node, selected).slice(0, 6)
  const raw = String(node.approver_display || node.resolve_message || node.approver_names || '').trim()
  if (!raw) return isNotifyPreviewNode(node) ? [] : ['提交后自动匹配']
  const people = visibleFlowNodePeople(
    node,
    raw.split(/[、,，;；\n]/).map((item) => item.trim()).filter(Boolean),
  )
  if (people.length) return people.slice(0, 6)
  return isNotifyPreviewNode(node) ? [] : ['提交后自动匹配']
}

function personInitial(name: string) {
  return Array.from(String(name || '').trim())[0] || '?'
}

function templateNeedsField(template: any, fieldType: string) {
  return templateFields(template).some((field: any) => String(field.field_type || '').toLowerCase() === fieldType)
}

async function loadOrganizationOptionsForTemplate(template: any) {
  const needsMember = templateNeedsField(template, 'member')
  const needsDepartment = templateNeedsField(template, 'department') || needsMember
  const needsCompany = templateNeedsField(template, 'company') || templateBusinessCode(template) === 'leave'
  const tasks: Promise<void>[] = []
  if (needsDepartment && departmentOptions.value.length === 0) {
    tasks.push(get('/departments').then((res) => { departmentOptions.value = normalizeList(res) }).catch(() => { departmentOptions.value = [] }))
  }
  if (needsMember && employeeOptions.value.length === 0) {
    tasks.push(get('/employees/selector', { limit: 1000 }).then((res) => { employeeOptions.value = normalizeEmployeeOptions(res) }).catch(() => { employeeOptions.value = [] }))
  }
  if (needsCompany && companyOptions.value.length === 0) {
    tasks.push(get('/payroll/companies', { active_only: true }).then((res) => { companyOptions.value = normalizeList(res) }).catch(() => { companyOptions.value = [] }))
  }
  if (tasks.length) await Promise.all(tasks)
}

function responseErrorMessage(error: any) {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || item?.message || String(item)).join('; ')
  }
  if (detail) return String(detail)
  return '提交失败，请检查表单或流程配置'
}

function derivedApplicationSummary() {
  if (summary.value.trim()) return summary.value.trim()
  if (!selectedTemplate.value) return ''
  const fields = templateFields(selectedTemplate.value)
  const reasonField = fields.find((field: any) => {
    const label = String(field.label || field.code || '')
    return isLongTextField(field) || /事由|原因|说明|备注/.test(label)
  })
  const reasonValue = reasonField ? formData.value[reasonField.code] : ''
  if (hasValue(reasonValue)) return String(reasonValue).trim()
  return selectedTemplate.value.name
}

async function submitApplication() {
  if (!selectedTemplate.value) return
  const missing = missingRequiredField.value
  if (missing) {
    await presentToast(`请先填写：${missing.label || missing.code}`, 'warning')
    return
  }
  if (!await validatePunchCorrectionBeforeSubmit()) {
    return
  }
  submitting.value = true
  try {
    const created = await post<any>('/approval/applications', {
      approval_type_id: selectedTemplate.value.id,
      summary: derivedApplicationSummary(),
      form_data: normalizedFormData(),
    })
    await presentToast('申请已提交')
    if (created?.id) {
      router.push(`/app/approval/${created.id}`)
    }
  } catch (error: any) {
    await presentToast(responseErrorMessage(error), 'danger')
  } finally {
    submitting.value = false
  }
}

async function enterApprovalStartPage() {
  await loadTemplates()
  if (routeQueryText('business_code')) {
    await openTemplateFromRoute()
  } else {
    await refreshSelectedTemplateFromLatestGroups()
  }
}

watch(formData, () => {
  scheduleFlowPreviewReload()
}, { deep: true })

onBeforeUnmount(() => {
  if (flowPreviewTimer) {
    clearTimeout(flowPreviewTimer)
    flowPreviewTimer = null
  }
})

onIonViewWillEnter(enterApprovalStartPage)
</script>

<style scoped>
.approval-start-page {
  min-height: 100%;
  padding: 0 0 86px;
  background: #eef2f8;
}

.wecom-approval-page {
  min-height: 100%;
  padding-bottom: 104px;
  background: #f1f2f6;
  color: #111111;
}

.wecom-form-header {
  position: sticky;
  top: 0;
  z-index: 12;
  height: 57px;
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr) 46px;
  align-items: center;
  background: #ffffff;
  border-bottom: 1px solid #edf0f5;
}

.wecom-form-header h1 {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: #000000;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 0;
  line-height: 1.2;
}

.wecom-form-icon-button {
  width: 46px;
  height: 57px;
  border: 0;
  background: transparent;
  color: #000000;
  display: grid;
  place-items: center;
  font-size: 27px;
}

.wecom-form-scroll {
  padding-bottom: 16px;
}

.wecom-form-notice,
.wecom-holiday-note,
.wecom-template-creator {
  background: #f1f2f6;
  color: #777b84;
  font-size: 15px;
  line-height: 1.55;
}

.wecom-form-notice {
  min-height: 58px;
  padding: 18px 15px;
  display: flex;
  align-items: center;
}

.wecom-form-section,
.wecom-flow-section {
  background: #ffffff;
}

.wecom-form-section {
  border-top: 1px solid #f0f1f5;
  border-bottom: 1px solid #f0f1f5;
}

.wecom-layout-field {
  min-height: 58px;
  padding: 12px 16px;
  border-bottom: 1px solid #edf0f5;
  background: #f6f7f9;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.wecom-layout-field span {
  border: 1px dashed #cfd6df;
  border-radius: 4px;
  background: #ffffff;
}

.wecom-field-row {
  width: 100%;
  min-height: 64px;
  padding: 0 16px;
  border: 0;
  border-bottom: 1px solid #edf0f5;
  background: #ffffff;
  color: #111111;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  text-align: left;
  letter-spacing: 0;
}

.wecom-field-row:last-child,
.wecom-duration-control:last-child .wecom-field-row:last-child {
  border-bottom: 0;
}

.wecom-field-group {
  border-bottom: 1px solid #edf0f5;
  background: #ffffff;
}

.wecom-field-group .wecom-field-row {
  border-bottom: 0;
}

.wecom-field-note {
  margin: -4px 16px 12px;
  color: #858b96;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  line-height: 20px;
}

.wecom-field-note button {
  flex: 0 0 auto;
  border: 0;
  padding: 0;
  background: transparent;
  color: #1677d2;
  font: inherit;
}

.wecom-field-note--danger {
  color: #d64f44;
}

.wecom-field-note--duration {
  margin-top: -10px;
}

.wecom-punch-monthly-note {
  margin-top: -3px;
  color: #8f949c;
  justify-content: flex-start;
}

.wecom-field-label {
  flex: 0 0 auto;
  color: #111111;
  font-size: 17px;
  font-weight: 500;
  line-height: 23px;
}

.wecom-field-label--required::after {
  content: ' *';
  color: #df6b64;
}

.wecom-field-value,
.wecom-duration-value {
  min-width: 0;
  flex: 1 1 auto;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  color: #8e8e93;
  font-size: 16px;
  line-height: 22px;
  text-align: right;
}

.wecom-field-value ion-icon {
  flex: 0 0 auto;
  color: #c8cbd1;
  font-size: 18px;
}

.wecom-field-value--placeholder {
  color: #9b9da3;
}

.wecom-row-input,
.wecom-row-select {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: #111111;
  text-align: right;
  font-size: 16px;
  line-height: 22px;
}

.wecom-row-input::placeholder {
  color: #9b9da3;
}

.wecom-row-select {
  appearance: none;
  color: #8e8e93;
}

.wecom-row-select:disabled {
  color: #b8bcc5;
}

.wecom-row-select--selected {
  color: #111111;
}

.wecom-textarea-field {
  min-height: 154px;
  padding: 19px 16px 18px;
  border-bottom: 1px solid #edf0f5;
  background: #ffffff;
  display: grid;
  gap: 14px;
}

.wecom-textarea-field textarea {
  width: 100%;
  min-height: 78px;
  border: 0;
  outline: 0;
  resize: none;
  background: transparent;
  color: #111111;
  font-size: 16px;
  line-height: 24px;
}

.wecom-textarea-field textarea::placeholder {
  color: #9b9da3;
}

.wecom-choice-field,
.wecom-attachment-control,
.wecom-file-attachment-control,
.wecom-detail-control,
.wecom-phone-control,
.wecom-account-control {
  padding: 16px;
  border-bottom: 1px solid #edf0f5;
  background: #ffffff;
  display: grid;
  gap: 12px;
}

.wecom-choice-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.wecom-choice-grid button,
.wecom-attachment-upload-button,
.wecom-detail-heading button {
  min-height: 38px;
  border: 1px solid #dfe3ea;
  border-radius: 4px;
  background: #ffffff;
  color: #30343b;
  font-size: 15px;
}

.wecom-choice-grid button.selected {
  border-color: #2f7ce8;
  background: #edf4ff;
  color: #2f7ce8;
  font-weight: 700;
}

.punch-eligibility-section {
  margin-top: 10px;
  padding: 13px 16px 15px;
  background: #ffffff;
  border-top: 1px solid #edf0f5;
  border-bottom: 1px solid #edf0f5;
  display: grid;
  gap: 10px;
}

.punch-eligibility-state {
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #4f5b6d;
  font-size: 14px;
}

.punch-eligibility-state.is-danger {
  display: grid;
  gap: 3px;
  color: #d0443e;
}

.punch-eligibility-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #d0443e;
  font-size: 14px;
}

.punch-eligibility-summary.is-ok {
  color: #1c8f53;
}

.punch-eligibility-summary strong {
  flex: 0 0 auto;
  font-size: 16px;
}

.punch-eligibility-summary span {
  min-width: 0;
  flex: 1 1 auto;
  text-align: right;
}

.punch-eligibility-meta,
.punch-eligibility-denies {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  color: #697386;
  font-size: 13px;
}

.punch-eligibility-meta span,
.punch-eligibility-denies span {
  padding: 5px 8px;
  border-radius: 4px;
  background: #f3f5f8;
}

.punch-eligibility-slots {
  display: grid;
  gap: 8px;
}

.punch-eligibility-slots button {
  min-height: 48px;
  padding: 8px 10px;
  border: 1px solid #dfe3ea;
  border-radius: 4px;
  background: #ffffff;
  color: #182033;
  text-align: left;
  display: grid;
  gap: 3px;
}

.punch-eligibility-slots button.selected {
  border-color: #2f7ce8;
  background: #eef5ff;
}

.punch-eligibility-slots button.is-disabled,
.punch-eligibility-slots button:disabled {
  border-color: #edf0f5;
  background: #f6f7fa;
  color: #a3a9b4;
}

.punch-eligibility-slots button.is-disabled span,
.punch-eligibility-slots button:disabled span {
  color: #a3a9b4;
}

.punch-eligibility-slots strong {
  font-size: 14px;
}

.punch-eligibility-slots span {
  color: #697386;
  font-size: 12px;
}

.wecom-attachment-upload-button {
  width: 100%;
  min-height: 28px;
  border: 0;
  background: transparent;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #111111;
  font-size: 17px;
  font-weight: 500;
  text-align: left;
}

.wecom-attachment-upload-button ion-icon {
  order: 2;
  color: #2f7ce8;
  font-size: 24px;
}

.wecom-attachment-upload-button span {
  order: 1;
}

.wecom-attachment-upload-button:disabled {
  opacity: 0.68;
}

.wecom-attachment-list {
  display: grid;
  gap: 8px;
}

.wecom-attachment-chip {
  min-height: 42px;
  padding: 6px 8px 6px 10px;
  border-radius: 4px;
  background: #f4f5f8;
  color: #575c66;
  font-size: 13px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.wecom-attachment-chip__link,
.wecom-attachment-chip__remove {
  border: 0;
  background: transparent;
}

.wecom-attachment-chip__link {
  min-width: 0;
  padding: 0;
  display: grid;
  gap: 2px;
  color: #30343b;
  text-align: left;
}

.wecom-attachment-chip__link span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wecom-attachment-chip__link small {
  color: #8d929b;
  font-size: 11px;
}

.wecom-attachment-chip__remove {
  min-height: 30px;
  padding: 0 4px;
  color: #d84f43;
  font-size: 12px;
}

.approval-hidden-file-input {
  display: none;
}

.wecom-duration-value {
  flex: 0 1 150px;
  color: #111111;
}

.wecom-duration-value input {
  width: 72px;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 500;
  text-align: right;
  pointer-events: none;
}

.wecom-duration-value span {
  flex: 0 0 auto;
}

.wecom-detail-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.wecom-detail-heading button {
  min-width: 62px;
  min-height: 24px;
  border: 0;
  background: transparent;
  padding: 0;
  color: #2f7ce8;
}

.wecom-detail-row {
  border-top: 1px solid #edf0f5;
  display: grid;
}

.wecom-detail-row label {
  min-height: 50px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.wecom-detail-row label + label {
  border-top: 1px solid #edf0f5;
}

.wecom-detail-row input,
.wecom-detail-row select,
.wecom-phone-control input,
.wecom-phone-control select,
.wecom-account-control input {
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: #111111;
  font-size: 15px;
}

.wecom-detail-row input,
.wecom-detail-row select {
  flex: 1 1 auto;
  text-align: right;
}

.wecom-detail-row input::placeholder {
  color: #9b9da3;
}

.wecom-detail-add {
  min-height: 44px;
  border: 0;
  border-top: 1px solid #edf0f5;
  background: transparent;
  color: #2f7ce8;
  padding: 0;
  text-align: left;
  font-size: 16px;
}

.wecom-detail-summary {
  min-height: 44px;
  border-top: 1px solid #edf0f5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: #8b9099;
  font-size: 15px;
}

.wecom-detail-summary strong {
  color: #111111;
  font-size: 18px;
  font-weight: 500;
}

.wecom-detail-summary-placeholder {
  color: #9b9da3 !important;
  font-size: 16px !important;
}

.wecom-phone-control > div {
  min-height: 42px;
  padding: 0 12px;
  border-radius: 4px;
  background: #f8f9fb;
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.wecom-phone-control select {
  border-right: 1px solid #e1e5ec;
}

.wecom-account-control input {
  min-height: 38px;
  border-bottom: 1px solid #edf0f5;
}

.wecom-account-control input:last-child {
  border-bottom: 0;
}

.wecom-static-text,
.wecom-holiday-note {
  margin: 0;
  padding: 16px;
  white-space: pre-wrap;
}

.wecom-holiday-note p {
  margin: 0;
}

.wecom-flow-section {
  padding: 22px 16px 28px;
}

.wecom-flow-section h2 {
  margin: 0;
  color: #111111;
  font-size: 18px;
  font-weight: 800;
  line-height: 1.25;
}

.wecom-flow-section > p {
  margin: 8px 0 24px;
  color: #9a9da4;
  font-size: 14px;
  line-height: 20px;
}

.wecom-flow-list {
  display: grid;
  gap: 0;
}

.wecom-flow-step {
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
  padding-bottom: 28px;
}

.wecom-flow-step:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 17px;
  top: 32px;
  bottom: 0;
  width: 1px;
  background: #e4e7ed;
}

.wecom-flow-dot {
  position: relative;
  z-index: 1;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: #d1d6df;
  color: #ffffff;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 800;
}

.wecom-flow-body {
  min-width: 0;
  display: grid;
  gap: 12px;
}

.wecom-flow-body strong {
  color: #111111;
  font-size: 17px;
  font-weight: 700;
  line-height: 24px;
}

.wecom-flow-people {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.wecom-person-chip {
  min-width: 0;
  max-width: 128px;
  min-height: 46px;
  padding: 6px 10px 6px 7px;
  border-radius: 4px;
  background: #f7f7f8;
  color: #111111;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.06);
  font-size: 15px;
}

.wecom-person-chip > span:last-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wecom-person-avatar {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border-radius: 4px;
  background: #dbe8ff;
  color: #2f7ce8;
  display: grid;
  place-items: center;
  font-size: 16px;
  font-weight: 700;
}

.wecom-person-add {
  width: 48px;
  height: 48px;
  border: 0;
  border-radius: 4px;
  background: #eef5ff;
  color: #2f7ce8;
  display: grid;
  place-items: center;
  font-size: 28px;
}

.wecom-flow-empty {
  color: #8c919b;
  font-size: 14px;
  line-height: 22px;
}

.wecom-template-creator {
  min-height: 72px;
  padding: 22px 16px;
  display: grid;
  place-items: center;
  text-align: center;
}

.wecom-submit-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 18;
  padding: 18px 20px calc(18px + env(safe-area-inset-bottom));
  border-top: 1px solid #edf0f5;
  background: #ffffff;
}

.wecom-submit-bar button {
  width: 100%;
  height: 50px;
  border: 0;
  border-radius: 4px;
  background: #2f7ce8;
  color: #ffffff;
  font-size: 18px;
  font-weight: 700;
}

.wecom-submit-bar button:disabled {
  opacity: 0.62;
}

.approval-template-content {
  --approval-page-bg: #eef2f8;
  --approval-card-bg: #ffffff;
  --approval-line: #dce3ec;
  --approval-line-strong: #d5dde8;
  --approval-blue: #25a8f2;
  --approval-yellow: #ffb72f;
  --approval-teal: #40cfc3;
  --background: var(--approval-page-bg);
}

.approval-template-page {
  min-height: 100%;
  color: #23262f;
}

.approval-template-header {
  position: relative;
  height: 58px;
  display: grid;
  place-items: center;
}

.approval-template-header h1 {
  margin: 0;
  color: #0f1724;
  font-size: 19px;
  font-weight: 800;
  line-height: 1;
  letter-spacing: 0;
}

.approval-template-back {
  position: absolute;
  left: 0;
  top: 0;
  width: 44px;
  height: 58px;
  border: 0;
  background: transparent;
  color: #050b16;
  display: grid;
  place-items: center;
  font-size: 31px;
}

.approval-template-search {
  height: 44px;
  margin: 2px 10px 12px;
  border-radius: 4px;
  background: var(--approval-card-bg);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  color: #8f98a6;
}

.approval-template-search ion-icon {
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
  font-size: 17px;
  line-height: 24px;
}

.approval-template-search input::placeholder {
  color: #9aa2af;
}

.approval-template-sections {
  display: grid;
  gap: 12px;
  padding: 0 10px 14px;
}

.approval-template-section {
  overflow: hidden;
  border-radius: 5px;
  background: var(--approval-card-bg);
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
  background: var(--approval-card-bg);
  color: #111827;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 12px;
  text-align: center;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.32;
  letter-spacing: 0;
  white-space: pre-line;
  -webkit-tap-highlight-color: transparent;
}

.approval-template-name {
  width: min(98px, calc(100% - 18px));
  margin: 0 auto;
  display: block;
  overflow-wrap: break-word;
  text-align: center;
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

.approval-template-loading,
.approval-template-empty {
  margin: 0 10px;
  min-height: 96px;
  border-radius: 4px;
  background: var(--approval-card-bg);
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

.approval-start-tabbar {
  box-sizing: border-box;
  position: fixed;
  left: 50%;
  right: auto;
  bottom: 0;
  z-index: 20;
  width: min(100%, var(--mobile-window-width));
  min-height: var(--mobile-bottom-nav-total-height);
  padding-bottom: env(safe-area-inset-bottom);
  border-top: 1px solid var(--mobile-bottom-nav-border);
  border-left: 1px solid var(--mobile-bottom-nav-side-border);
  border-right: 1px solid var(--mobile-bottom-nav-side-border);
  background: var(--mobile-bottom-nav-background);
  backdrop-filter: blur(18px);
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  transform: translateX(-50%);
}

.approval-start-tabbar__item {
  min-height: var(--mobile-bottom-nav-height);
  border: 0;
  background: transparent;
  color: var(--mobile-bottom-nav-color);
  display: grid;
  justify-items: center;
  align-content: center;
  gap: 4px;
  font-size: var(--mobile-bottom-nav-label-size);
  font-weight: var(--mobile-bottom-nav-font-weight);
  line-height: 15px;
}

.approval-start-tabbar__item ion-icon {
  font-size: var(--mobile-bottom-nav-icon-size);
}

.approval-start-tabbar__item--active {
  color: var(--mobile-bottom-nav-active-color);
}

.approval-hero {
  padding: 18px;
  background:
    linear-gradient(135deg, rgba(37, 99, 235, 0.1), rgba(20, 184, 166, 0.1)),
    #ffffff;
}

.approval-hero__eyebrow {
  display: inline-flex;
  margin-bottom: 8px;
  color: #2563eb;
  font-size: 12px;
  font-weight: 800;
}

.approval-hero h2 {
  margin: 0;
  color: #0f172a;
  font-size: 22px;
  line-height: 1.25;
}

.approval-hero p {
  margin: 10px 0 0;
  color: #475569;
  font-size: 14px;
  line-height: 1.6;
}

.guide-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
}

.guide-steps span {
  min-height: 34px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
  font-size: 12px;
  font-weight: 700;
}

.guide-steps .guide-step--done {
  background: #ecfdf5;
  color: #047857;
  border-color: #bbf7d0;
}

.loading-card,
.empty-card,
.no-selection-card {
  padding: 22px;
  display: grid;
  gap: 10px;
  justify-items: center;
  text-align: center;
}

.loading-card {
  grid-template-columns: auto 1fr;
  justify-items: start;
  text-align: left;
  color: #475569;
}

.template-section {
  padding: 16px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.section-heading h3 {
  margin: 0;
  color: #0f172a;
  font-size: 16px;
  font-weight: 800;
}

.section-heading p,
.empty-card span,
.no-selection-card span {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.5;
}

.section-heading > span {
  flex: 0 0 auto;
  padding: 5px 9px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-size: 12px;
  font-weight: 700;
}

.template-grid {
  display: grid;
  gap: 10px;
}

.template-card {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  text-align: left;
  padding: 13px 12px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 8px;
  background: #fff;
  color: #0f172a;
}

.template-card__main {
  min-width: 0;
  display: grid;
  gap: 5px;
}

.template-card__main strong {
  font-size: 15px;
}

.template-card__main small {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.template-card__state {
  flex: 0 0 auto;
  min-width: 54px;
  min-height: 28px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  font-weight: 700;
}

.template-card--active {
  border-color: #2563eb;
  background: #f8fbff;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.08);
}

.template-card--active .template-card__state {
  background: #2563eb;
  color: #ffffff;
}

.form-card {
  padding: 18px 16px;
}

.selected-template-banner,
.form-guidance,
.submit-panel {
  border-radius: 8px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
}

.selected-template-banner {
  padding: 13px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.selected-template-banner div,
.form-guidance div,
.submit-panel div {
  min-width: 0;
  display: grid;
  gap: 5px;
}

.selected-template-banner span,
.form-guidance span,
.submit-panel span {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.selected-template-banner strong,
.form-guidance strong,
.submit-panel strong {
  color: #0f172a;
  font-size: 15px;
}

.selected-template-banner button {
  border: 0;
  background: transparent;
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
}

.form-guidance {
  margin-top: 12px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.form-guidance > span {
  flex: 0 0 auto;
  min-width: 46px;
  min-height: 30px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: #ecfdf5;
  color: #047857;
  font-weight: 800;
}

.approval-form-list {
  margin-top: 18px;
  background: transparent;
}

.approval-form-item {
  --background: transparent;
  --padding-start: 0;
  --inner-padding-end: 0;
  --inner-padding-start: 0;
  --min-height: 0;
  --border-width: 0;
  margin-top: 18px;
}

.approval-form-item:first-child {
  margin-top: 0;
}

.flow-preview-panel {
  margin-top: 18px;
  padding: 14px;
  border: 1px solid #dbeafe;
  border-radius: 8px;
  background: #f8fbff;
}

.flow-preview-panel__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.flow-preview-panel__heading div {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.flow-preview-panel__heading strong {
  color: #0f172a;
  font-size: 15px;
}

.flow-preview-panel__heading span,
.flow-preview-empty,
.flow-node__body p {
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

.flow-node-list {
  margin-top: 12px;
  display: grid;
  gap: 10px;
}

.flow-node {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
}

.flow-node__order {
  width: 30px;
  height: 30px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: #2563eb;
  color: #ffffff;
  font-size: 13px;
  font-weight: 800;
}

.flow-node__body {
  min-width: 0;
  padding-bottom: 10px;
  border-bottom: 1px solid #e2e8f0;
}

.flow-node:last-child .flow-node__body {
  padding-bottom: 0;
  border-bottom: 0;
}

.flow-node__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.flow-node__title strong {
  min-width: 0;
  color: #0f172a;
  font-size: 14px;
}

.flow-node__title span {
  flex: 0 0 auto;
  padding: 3px 7px;
  border-radius: 999px;
  background: #e0f2fe;
  color: #0369a1;
  font-size: 11px;
  font-weight: 800;
}

.flow-node__body p,
.flow-preview-empty {
  margin: 6px 0 0;
}

.field-block {
  width: 100%;
  display: grid;
  gap: 8px;
}

.field-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #0f172a;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.35;
}

.field-label--required::after {
  content: '*';
  color: #dc2626;
  font-weight: 800;
}

.approval-control {
  width: 100%;
  --background: #ffffff;
  --border-color: #cbd5e1;
  --border-radius: 8px;
  --border-width: 1px;
  --highlight-color-focused: #2563eb;
  --padding-start: 14px;
  --padding-end: 14px;
  --padding-top: 12px;
  --padding-bottom: 12px;
  color: #0f172a;
  font-size: 16px;
  line-height: 1.45;
}

.approval-control--textarea {
  min-height: 104px;
}

.org-picker-trigger {
  min-height: 48px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  text-align: left;
}

.org-picker-placeholder {
  color: #94a3b8;
}

.org-picker-chevron {
  flex: 0 0 auto;
  color: #2563eb;
  font-size: 13px;
  font-weight: 800;
}

.org-picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: flex-end;
}

.org-picker-sheet {
  width: 100%;
  max-height: 78vh;
  border-radius: 8px 8px 0 0;
  background: #ffffff;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  overflow: hidden;
}

.org-picker-header {
  padding: 14px 16px;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.org-picker-header div {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.org-picker-header span {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.org-picker-header strong {
  color: #0f172a;
  font-size: 16px;
}

.org-picker-header button {
  border: 0;
  background: transparent;
  color: #2563eb;
  font-size: 14px;
  font-weight: 800;
}

.org-picker-body {
  overflow: auto;
  padding: 8px 0 18px;
}

.org-picker-row {
  width: 100%;
  min-height: 44px;
  border: 0;
  border-bottom: 1px solid #f1f5f9;
  background: #ffffff;
  color: #0f172a;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
}

.org-picker-row--employee {
  color: #334155;
}

.org-picker-row--selected {
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 800;
}

.org-picker-caret {
  width: 18px;
  flex: 0 0 18px;
  color: #64748b;
  font-size: 11px;
}

.org-picker-row-label {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.org-picker-row small {
  flex: 0 0 auto;
  max-width: 34%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
  font-size: 12px;
}

.org-picker-empty {
  padding: 28px 16px;
  color: #64748b;
  text-align: center;
  font-size: 14px;
}

.org-picker-footer {
  min-height: 56px;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.org-picker-footer span {
  color: #64748b;
  font-size: 13px;
}

.org-picker-footer button {
  min-width: 88px;
  height: 36px;
  border: 0;
  border-radius: 4px;
  background: #2563eb;
  color: #ffffff;
  font-size: 14px;
  font-weight: 800;
}

.company-picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 55;
  background: rgba(0, 0, 0, 0.28);
  display: flex;
  align-items: flex-end;
}

.company-picker-sheet {
  width: 100%;
  max-height: 68vh;
  border-radius: 10px 10px 0 0;
  background: #ffffff;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}

.company-picker-header {
  min-height: 54px;
  padding: 0 16px;
  border-bottom: 1px solid #edf0f5;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) 72px;
  align-items: center;
}

.company-picker-header strong {
  min-width: 0;
  overflow: hidden;
  color: #111827;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 16px;
  font-weight: 700;
}

.company-picker-header button {
  border: 0;
  background: transparent;
  color: #2f7ce8;
  font-size: 16px;
}

.company-picker-header button:first-child {
  justify-self: start;
  color: #8d96a3;
}

.company-picker-header button:last-child {
  justify-self: end;
}

.company-picker-body {
  overflow: auto;
  padding-bottom: env(safe-area-inset-bottom);
}

.company-picker-row {
  width: 100%;
  min-height: 54px;
  padding: 0 20px;
  border: 0;
  border-bottom: 1px solid #f0f2f6;
  background: #ffffff;
  color: #111827;
  display: flex;
  align-items: center;
  gap: 14px;
  text-align: left;
  font-size: 17px;
}

.company-picker-row--selected {
  font-weight: 700;
}

.company-picker-check {
  width: 22px;
  height: 22px;
  border: 1px solid #c7ccd5;
  border-radius: 999px;
  color: #ffffff;
  display: grid;
  place-items: center;
  font-size: 14px;
}

.company-picker-row--selected .company-picker-check {
  border-color: #2f7ce8;
  background: #2f7ce8;
}

.company-picker-empty {
  padding: 28px 16px;
  color: #8d96a3;
  text-align: center;
  font-size: 14px;
}

.leave-type-picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 56;
  background: rgba(0, 0, 0, 0.28);
  display: flex;
  align-items: flex-end;
}

.leave-type-picker-sheet {
  width: 100%;
  max-height: 72vh;
  border-radius: 10px 10px 0 0;
  background: #ffffff;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}

.leave-type-picker-header {
  min-height: 54px;
  padding: 0 16px;
  border-bottom: 1px solid #edf0f5;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) 72px;
  align-items: center;
}

.leave-type-picker-header strong {
  min-width: 0;
  overflow: hidden;
  color: #111827;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 16px;
  font-weight: 700;
}

.leave-type-picker-header button {
  border: 0;
  background: transparent;
  color: #2f7ce8;
  font-size: 16px;
}

.leave-type-picker-header button:first-child {
  justify-self: start;
  color: #111827;
}

.leave-type-picker-header button:last-child {
  justify-self: end;
}

.leave-type-picker-body {
  overflow: auto;
  padding-bottom: env(safe-area-inset-bottom);
}

.leave-type-picker-row {
  width: 100%;
  min-height: 68px;
  padding: 0 18px 0 20px;
  border: 0;
  border-bottom: 1px solid #f0f2f6;
  background: #ffffff;
  color: #111827;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  text-align: left;
}

.leave-type-picker-row span:first-child {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.leave-type-picker-row strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 17px;
  font-weight: 500;
}

.leave-type-picker-row small {
  color: #8d96a3;
  font-size: 13px;
}

.leave-type-picker-row--selected strong {
  font-weight: 700;
}

.leave-type-picker-check {
  width: 24px;
  flex: 0 0 24px;
  color: #2f7ce8;
  text-align: center;
  font-size: 26px;
  line-height: 1;
}

.leave-type-picker-empty {
  padding: 28px 16px;
  color: #8d96a3;
  text-align: center;
  font-size: 14px;
}

.approval-datetime-modal {
  --width: 100%;
  --max-width: var(--mobile-window-width);
  --height: auto;
  --border-radius: 10px 10px 0 0;
  align-items: flex-end;
}

.approval-datetime-sheet {
  width: 100%;
  padding-bottom: calc(10px + env(safe-area-inset-bottom));
  border-radius: 10px 10px 0 0;
  background: #ffffff;
  overflow: hidden;
}

.approval-datetime-header {
  min-height: 52px;
  padding: 0 16px;
  border-bottom: 1px solid #edf0f5;
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 64px;
  align-items: center;
  gap: 10px;
}

.approval-datetime-header strong {
  min-width: 0;
  overflow: hidden;
  color: #111827;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 17px;
  font-weight: 800;
}

.approval-datetime-header button {
  height: 40px;
  border: 0;
  background: transparent;
  color: #2f7ce8;
  font-size: 16px;
  font-weight: 700;
}

.approval-datetime-header button:first-child {
  color: #6b7280;
  text-align: left;
}

.approval-datetime-header button:last-child {
  text-align: right;
}

.approval-datetime-control {
  width: 100%;
  max-width: none;
  margin: 0 auto;
  --background: #ffffff;
  color: #111827;
}

.field-hint {
  margin: 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}

.choice-control,
.range-control,
.duration-control,
.attachment-control,
.detail-control,
.phone-control,
.account-control {
  width: 100%;
  display: grid;
  gap: 10px;
}

.choice-control {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.choice-control--single {
  grid-template-columns: 1fr;
}

.choice-control button,
.attachment-control button,
.detail-control button {
  min-height: 42px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  color: #334155;
  font-size: 14px;
}

.choice-control button.selected {
  border-color: #2563eb;
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 800;
}

.range-control label,
.duration-control label {
  min-height: 46px;
  padding: 0 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.range-control span,
.duration-control span {
  color: #64748b;
  font-size: 13px;
}

.range-control input,
.duration-control input,
.phone-control input,
.phone-control select,
.account-control input,
.detail-row input {
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: #0f172a;
  text-align: right;
  font-size: 15px;
}

.location-trigger,
.related-approval-trigger {
  min-height: 48px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  text-align: left;
}

.location-trigger > span:last-child,
.related-approval-trigger > span:last-child {
  flex: 0 0 auto;
  color: #2563eb;
  font-size: 13px;
  font-weight: 800;
}

.attachment-control button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  color: #2563eb;
  font-weight: 800;
}

.attachment-chip {
  min-height: 32px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #f1f5f9;
  color: #475569;
  font-size: 13px;
}

.detail-control {
  padding: 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
}

.detail-row {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) minmax(0, 0.8fr);
  gap: 8px;
  align-items: center;
}

.detail-row span {
  color: #64748b;
  font-size: 13px;
}

.detail-row input {
  height: 34px;
  padding: 0 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  text-align: left;
}

.detail-row--custom {
  display: grid;
  grid-template-columns: 1fr;
  align-items: stretch;
}

.detail-row--custom > span {
  font-weight: 800;
}

.detail-child-input {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.detail-child-input span {
  color: #64748b;
  font-size: 13px;
}

.detail-row--custom .detail-child-input input,
.detail-row--custom .detail-child-input select {
  min-width: 0;
  height: 34px;
  padding: 0 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  outline: 0;
  background: #ffffff;
  color: #0f172a;
  text-align: left;
  font-size: 15px;
}

.phone-control {
  grid-template-columns: 88px minmax(0, 1fr);
  min-height: 48px;
  padding: 0 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  align-items: center;
}

.phone-control select {
  text-align: left;
  border-right: 1px solid #e2e8f0;
  height: 26px;
}

.account-control {
  padding: 10px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
}

.account-control input {
  height: 36px;
  border-bottom: 1px solid #edf2f7;
  text-align: left;
}

.account-control input:last-child {
  border-bottom: 0;
}

.static-field-text {
  margin: 0;
  padding: 10px 12px;
  border-left: 3px solid #2563eb;
  border-radius: 8px;
  background: #f8fafc;
  color: #475569;
  font-size: 14px;
  line-height: 1.6;
}

.submit-panel {
  margin-top: 16px;
  padding: 14px;
  display: grid;
  gap: 12px;
}
</style>
