<template>
  <div class="wecom-approval-page">
    <main class="wecom-main">
      <template v-if="screen === 'overview'">
        <section class="wecom-card approval-form-manager" :style="approvalFormManagerStyle">
          <div ref="formManagerStickyRef" class="form-manager-sticky">
            <nav v-if="canManageTemplateConfigs" class="approval-overview-tabs" aria-label="审批管理模块">
              <button
                type="button"
                :class="{ active: activeOverviewSection === 'forms' }"
                :aria-current="activeOverviewSection === 'forms' ? 'page' : undefined"
                @click="switchOverviewSection('forms')"
              >
                表单管理
              </button>
              <button
                type="button"
                :class="{ active: activeOverviewSection === 'data' }"
                :aria-current="activeOverviewSection === 'data' ? 'page' : undefined"
                @click="switchOverviewSection('data')"
              >
                数据管理
              </button>
            </nav>

            <template v-if="activeOverviewSection === 'forms'">
              <header class="form-manager-header">
                <div>
                  <h1 data-testid="approval-page-title">表单管理</h1>
                </div>
                <div class="form-manager-actions">
                  <label class="form-search">
                    <component :is="Search" aria-hidden="true" />
                    <input v-model="templateSearchKeyword" type="search" placeholder="搜索表单" />
                  </label>
                  <template v-if="canManageTemplateConfigs">
                    <button
                      class="secondary-pill-btn"
                      :class="{ active: templateBatchMode }"
                      type="button"
                      @click="batchDisableSelectedTemplates"
                    >
                      <component :is="CircleClose" aria-hidden="true" />
                      <span>{{ templateBatchMode && selectedTemplateIds.length ? `停用已选(${selectedTemplateIds.length})` : '批量停用' }}</span>
                    </button>
                    <button
                      v-if="templateBatchMode"
                      class="secondary-pill-btn"
                      type="button"
                      @click="exitTemplateBatchMode"
                    >
                      <span>取消批量</span>
                    </button>
                    <button class="secondary-pill-btn" type="button" @click="createTemplateGroup">
                      <component :is="CirclePlus" aria-hidden="true" />
                      <span>新建分组</span>
                    </button>
                    <button
                      class="primary-split-btn add-template-btn"
                      type="button"
                      data-testid="add-template-btn"
                      @click="addDialogVisible = true"
                    >
                      <component :is="Plus" aria-hidden="true" />
                      <span>创建审批表单</span>
                      <i></i>
                      <component :is="ArrowDown" aria-hidden="true" />
                    </button>
                  </template>
                </div>
              </header>

              <div class="template-category-bar">
                <nav class="template-category-tabs" aria-label="审批表单分类">
                  <button
                    v-for="category in templateCategoryTabs"
                    :key="category.name"
                    type="button"
                    :aria-selected="activeTemplateCategory === category.name"
                    @click="focusTemplateCategory(category.name)"
                  >
                    <span>{{ category.name }}</span>
                  </button>
                </nav>
                <button class="group-sort-btn" type="button" @click="sortAllTemplateGroups">
                  <component :is="Sort" aria-hidden="true" />
                  <span>分组排序</span>
                </button>
              </div>

              <p v-if="!canManageTemplateConfigs" class="muted readonly-tip">当前角色仅可查看模板列表，模板配置仅对管理员和 HR 开放。</p>
            </template>
          </div>

          <template v-if="activeOverviewSection === 'forms'">
            <p v-if="!visibleTemplateGroups.length" class="empty-tip">暂无匹配表单</p>

            <section
              v-for="group in visibleTemplateGroups"
              :key="group.name"
              class="template-group-panel"
              :class="{
                collapsed: !isTemplateGroupExpanded(group.name),
                'template-group-panel--menu-open': group.items.some(isTemplateMoreOpen),
              }"
              :data-template-group="group.name"
            >
            <header class="template-group-header">
              <button class="template-group-title" type="button" @click="toggleTemplateGroup(group.name)">
                <component :is="isTemplateGroupExpanded(group.name) ? ArrowDown : ArrowRight" aria-hidden="true" />
                <strong>{{ group.name }}</strong>
                <span>({{ group.items.length }})</span>
              </button>
              <div class="template-group-actions" v-if="canManageTemplateConfigs">
                <button type="button" @click="sortTemplateGroup(group.name)">
                  <component :is="Sort" aria-hidden="true" />
                  <span>表单排序</span>
                </button>
                <button type="button" @click="configureTemplateGroup(group.name)">
                  <component :is="Setting" aria-hidden="true" />
                  <span>分组设置</span>
                </button>
              </div>
            </header>

            <div
              v-show="isTemplateGroupExpanded(group.name)"
              class="template-table-shell"
              :class="{ 'template-table-shell--menu-open': group.items.some(isTemplateMoreOpen) }"
            >
              <table class="template-table">
                <thead>
                  <tr>
                    <th>表单</th>
                    <th>谁可以发起</th>
                    <th>最后更新</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="item in group.items"
                    :key="`${group.name}-${item.id || item.name}`"
                    data-testid="template-row"
                    :data-template-name="item.name"
                    :class="{
                      muted: item.disabled,
                      focused: item.focused,
                      'template-row--readonly': !canManageTemplateConfigs,
                      'template-row--menu-open': isTemplateMoreOpen(item),
                    }"
                    @click="handleTemplateSelection(item)"
                  >
                    <td>
                      <div class="template-name">
                        <label v-if="templateBatchMode && canManageTemplateConfigs && item.id" class="template-check" @click.stop>
                          <input
                            type="checkbox"
                            :checked="isTemplateSelected(item)"
                            :aria-label="`选择${item.name}`"
                            @change="toggleTemplateSelection(item, $event)"
                          />
                        </label>
                        <span class="template-icon" :class="item.color || templateColorForName(item.name)">
                          <component :is="item.icon || templateIconForName(item.name)" />
                        </span>
                        <span class="template-title">
                          <span class="template-title-line">
                            <strong>{{ item.name }}</strong>
                            <button
                              v-if="canManageTemplateConfigs"
                              class="row-mini-action"
                              type="button"
                              aria-label="编辑表单"
                              title="编辑表单"
                              @click.stop="openTemplate(item)"
                            >
                              <component :is="EditPen" aria-hidden="true" />
                            </button>
                            <span v-if="item.is_active === false" class="template-inline-state">已停用</span>
                            <span v-else-if="!item.id" class="template-inline-state template-inline-state--draft">待创建</span>
                          </span>
                          <small v-if="item.note">{{ item.note }}</small>
                        </span>
                      </div>
                    </td>
                    <td>
                      <span class="template-scope-cell">
                        <span>{{ templateScopeText(item) }}</span>
                        <button
                          v-if="canManageTemplateConfigs && item.id"
                          class="row-mini-action"
                          type="button"
                          aria-label="配置可发起范围"
                          title="配置可发起范围"
                          @click.stop="openTemplate(item)"
                        >
                          <component :is="EditPen" aria-hidden="true" />
                        </button>
                      </span>
                    </td>
                    <td>{{ templateUpdatedText(item) }}</td>
                    <td>
                      <div class="template-table-actions" v-if="canManageTemplateConfigs">
                        <button type="button" class="plain-link" @click.stop="openTemplate(item)">
                          <component :is="EditPen" aria-hidden="true" />
                          <span>编辑</span>
                        </button>
                        <button v-if="item.id" type="button" class="plain-link" @click.stop="exportTemplate(item)">
                          <component :is="Download" aria-hidden="true" />
                          <span>导出</span>
                        </button>
                        <span v-if="item.more" class="template-more-wrap" @click.stop>
                          <button
                            type="button"
                            class="plain-link"
                            :aria-expanded="isTemplateMoreOpen(item)"
                            @click.stop="toggleTemplateMore(item)"
                          >
                            <span>更多</span>
                          </button>
                          <transition name="template-more-pop">
                            <div
                              v-if="isTemplateMoreOpen(item)"
                              class="template-more-menu"
                              role="menu"
                              @click.stop
                            >
                              <button type="button" role="menuitem" @click.stop="handleTemplateMoreAction(item, 'copy')">复制</button>
                              <button type="button" role="menuitem" @click.stop="handleTemplateMoreAction(item, 'move')">移动到</button>
                              <button
                                type="button"
                                role="menuitem"
                                :disabled="item.is_active === false"
                                @click.stop="handleTemplateMoreAction(item, 'disable')"
                              >
                                停用
                              </button>
                            </div>
                          </transition>
                        </span>
                      </div>
                    </td>
                  </tr>
                  <tr v-if="!group.items.length">
                    <td colspan="4" class="template-empty-row">该分组暂无表单</td>
                  </tr>
                </tbody>
              </table>
            </div>
            </section>
          </template>

          <section v-if="canManageTemplateConfigs" v-show="activeOverviewSection === 'data'" class="archive-manager archive-manager--overview">
          <div class="archive-header">
            <h2>数据管理</h2>
            <span class="muted">归档所有审批数据</span>
          </div>
          <div class="filter-grid archive-filter-grid">
            <label><span>开始日期</span><input v-model="archiveFilters.start_date" type="date" /></label>
            <label><span>结束日期</span><input v-model="archiveFilters.end_date" type="date" /></label>
            <label>
              <span>审批类型</span>
              <select v-model="archiveFilters.business_type">
                <option value="">全部类型</option>
                <option v-for="option in approvalArchiveTypeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>
            <label>
              <span>审批状态</span>
              <select v-model="archiveFilters.status">
                <option value="">全部状态</option>
                <option value="pending">待审批</option>
                <option value="approved">已通过</option>
                <option value="rejected">已拒绝</option>
                <option value="withdrawn">已撤回</option>
                <option value="cancelled">已取消</option>
              </select>
            </label>
            <label class="archive-applicant-filter">
              <span>申请人</span>
              <span class="archive-applicant-control">
                <button class="archive-applicant-trigger" type="button" @click="openArchiveApplicantPicker">
                  <span :class="{ 'archive-applicant-placeholder': !archiveApplicantDisplay }">
                    {{ archiveApplicantDisplay || '从组织架构选择申请人' }}
                  </span>
                  <span aria-hidden="true">⌄</span>
                </button>
                <button
                  v-if="archiveFilters.applicant_id"
                  class="archive-applicant-clear"
                  type="button"
                  @click="clearArchiveApplicantFilter"
                >清除</button>
              </span>
            </label>
            <div class="archive-filter-actions">
              <button type="button" class="primary-btn" @click="loadOverviewArchive">查询</button>
              <button type="button" class="secondary-btn" @click="resetApprovalArchiveFilters">重置</button>
            </div>
          </div>
          <div class="record-tools">
            <h3>共 {{ approvalArchiveTotal }} 条审批记录</h3>
          </div>
          <table class="record-table">
            <thead>
              <tr><th>审批编号</th><th>审批类型</th><th>提交时间</th><th>完成时间</th><th>申请人</th><th>当前节点</th><th>审批状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="record in records" :key="record.id">
                <td>{{ approvalRecordNo(record) }}</td>
                <td>{{ approvalRecordType(record) }}</td>
                <td>{{ formatApprovalDateTime(record.created_at) }}</td>
                <td>{{ approvalRecordFinish(record) }}</td>
                <td>{{ approvalRecordApplicant(record) }}</td>
                <td>{{ approvalRecordNode(record) }}</td>
                <td>{{ approvalStatusText(record.status) }}</td>
                <td><button type="button" class="plain-link" @click="openApprovalRecord(record)">查看</button></td>
              </tr>
              <tr v-if="!records.length">
                <td colspan="8" class="record-empty">{{ approvalArchiveLoading ? '正在加载审批记录' : '暂无审批记录' }}</td>
              </tr>
            </tbody>
          </table>
          </section>
        </section>
      </template>

      <section v-else class="wecom-card settings-page">
        <header class="approval-editor-topbar">
          <div class="approval-editor-title">
            <button class="editor-back-btn" type="button" aria-label="返回模板管理" @click="returnToApprovalOverview">‹</button>
            <span class="editor-template-icon template-icon" :class="currentTemplateColor">
              <component :is="currentTemplateIcon" />
            </span>
            <span class="editor-title-text">
              <strong>{{ currentTemplate.name }}</strong>
              <small>{{ editorSaveStateText }}</small>
            </span>
            <button class="editor-title-caret" type="button" aria-label="展开模板信息">
              <component :is="ArrowDown" aria-hidden="true" />
            </button>
          </div>

          <nav class="approval-editor-steps" role="tablist" aria-label="模板详情">
            <button
              v-for="(step, index) in editorSteps"
              :key="step.value"
              type="button"
              role="tab"
              :data-testid="`tab-${step.value}`"
              :aria-selected="editorStep === step.value"
              @click="switchEditorStep(step.value)"
            >
              <span>{{ index + 1 }}</span>
              <strong>{{ step.label }}</strong>
            </button>
          </nav>

          <div class="approval-editor-actions">
            <button class="editor-help-btn" type="button" @click="showDesignerHelp">
              <span aria-hidden="true">?</span>
              帮助
            </button>
            <button class="editor-preview-btn" type="button" @click="openApprovalPreview">预览</button>
            <button class="editor-preview-btn" type="button" @click="saveServerDraft">保存草稿</button>
            <button v-if="draftTemplateSnapshot" class="editor-preview-btn" type="button" @click="selectDraftFlowVersion">继续草稿</button>
            <button class="editor-publish-btn" type="button" @click="saveTemplateConfig">发布</button>
          </div>
        </header>

        <div v-if="settingsTab === 'records'" class="records-view">
          <div class="filter-grid">
            <label><span>开始日期</span><input v-model="archiveFilters.start_date" type="date" /></label>
            <label><span>结束日期</span><input v-model="archiveFilters.end_date" type="date" /></label>
            <label>
              <span>审批状态</span>
              <select v-model="archiveFilters.status">
                <option value="">全部状态</option>
                <option value="pending">待审批</option>
                <option value="approved">已通过</option>
                <option value="rejected">已拒绝</option>
                <option value="withdrawn">已撤回</option>
                <option value="cancelled">已取消</option>
              </select>
            </label>
            <label class="archive-applicant-filter">
              <span>申请人</span>
              <span class="archive-applicant-control">
                <button class="archive-applicant-trigger" type="button" @click="openArchiveApplicantPicker">
                  <span :class="{ 'archive-applicant-placeholder': !archiveApplicantDisplay }">
                    {{ archiveApplicantDisplay || '从组织架构选择申请人' }}
                  </span>
                  <span aria-hidden="true">⌄</span>
                </button>
                <button
                  v-if="archiveFilters.applicant_id"
                  class="archive-applicant-clear"
                  type="button"
                  @click="clearArchiveApplicantFilter"
                >清除</button>
              </span>
            </label>
            <div class="archive-filter-actions">
              <button type="button" class="primary-btn" @click="loadCurrentTemplateRecords">查询</button>
              <button type="button" class="secondary-btn" @click="resetApprovalArchiveFilters">重置</button>
            </div>
          </div>
          <div class="record-tools">
            <h3>共 {{ approvalArchiveTotal }} 条申请记录</h3>
            <div>
              <button type="button" class="secondary-btn" @click="exportCurrentTemplateAttachmentManifest">导出附件清单</button>
              <button type="button" class="primary-btn" @click="exportCurrentTemplateRecords">导出记录</button>
            </div>
          </div>
          <table class="record-table">
            <thead>
              <tr><th>审批编号</th><th>审批类型</th><th>提交时间</th><th>完成时间</th><th>申请人</th><th>当前节点</th><th>审批状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="record in records" :key="record.id">
                <td>{{ approvalRecordNo(record) }}</td>
                <td>{{ approvalRecordType(record) }}</td>
                <td>{{ formatApprovalDateTime(record.created_at) }}</td>
                <td>{{ approvalRecordFinish(record) }}</td>
                <td>{{ approvalRecordApplicant(record) }}</td>
                <td>{{ approvalRecordNode(record) }}</td>
                <td>{{ approvalStatusText(record.status) }}</td>
                <td><button type="button" class="plain-link" @click="openApprovalRecord(record)">查看</button></td>
              </tr>
              <tr v-if="!records.length">
                <td colspan="8" class="record-empty">{{ approvalArchiveLoading ? '正在加载审批记录' : '暂无申请记录' }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-else-if="editorStep === 'basic'" class="basic-settings approval-step-panel">
          <section class="basic-card" aria-label="审批模板基础设置">
            <div class="basic-identity-row">
              <button class="basic-icon-picker" type="button" aria-label="编辑表单图标">
                <span class="template-icon" :class="currentTemplateColor">
                  <component :is="currentTemplateIcon" />
                </span>
                <span class="basic-icon-edit" aria-hidden="true"><component :is="EditPen" /></span>
              </button>
              <label class="basic-field basic-field--name">
                <span><b>*</b> 表单名称</span>
                <span class="basic-input-wrap">
                  <input v-model="currentTemplate.name" maxlength="50" aria-label="表单名称" />
                  <small>{{ currentTemplate.name.length }} / 50</small>
                </span>
              </label>
            </div>

            <label class="basic-field">
              <span><b>*</b> 所在分组</span>
              <select v-model="currentTemplate.group" aria-label="所在分组">
                <option v-for="groupName in templateGroupOptions" :key="groupName" :value="groupName">{{ groupName }}</option>
              </select>
            </label>

            <fieldset class="basic-radio-block basic-scope-block">
              <legend><b>*</b> 可见范围</legend>
              <div class="basic-scope-summary">
                <span class="dept-chip">
                  <span class="folder-icon"></span>
                  {{ visibleScopeLabel }}
                </span>
                <button class="basic-select-link" type="button" @click="openRuleDialog('visible')">配置可见范围</button>
              </div>
            </fieldset>

            <label class="basic-field basic-field--textarea">
              <span>表单说明</span>
              <span class="basic-textarea-wrap">
                <textarea v-model="currentTemplate.description" maxlength="100" rows="4" aria-label="表单说明" />
                <small>{{ currentTemplate.description.length }} / 100</small>
              </span>
            </label>

            <fieldset class="basic-radio-block">
              <legend><b>*</b> 谁可以发起</legend>
              <div class="basic-radio-line">
                <label><input v-model="ruleSettings.submitPermissionType" type="radio" value="all" @change="markRulesDirty" /> 全部</label>
                <label><input v-model="ruleSettings.submitPermissionType" type="radio" value="selected_members" @change="markRulesDirty" /> 指定成员</label>
                <button
                  v-if="ruleSettings.submitPermissionType === 'selected_members'"
                  class="basic-select-link"
                  type="button"
                  @click="openRuleDialog('submit')"
                >{{ selectedSubmitterLabel }}</button>
              </div>
            </fieldset>

            <fieldset class="basic-radio-block">
              <legend>
                <b>*</b> 表单管理员
                <span class="basic-help" title="表单管理员可维护该模板的基础设置、表单设计和流程设计">?</span>
              </legend>
              <div class="basic-radio-line">
                <label><input v-model="ruleSettings.templateAdminType" type="radio" value="all" @change="markRulesDirty" /> 全部「OA审批」管理员</label>
                <label><input v-model="ruleSettings.templateAdminType" type="radio" value="selected" @change="markRulesDirty" /> 指定「OA审批」管理员</label>
                <button
                  v-if="ruleSettings.templateAdminType === 'selected'"
                  class="basic-select-link"
                  type="button"
                  @click="openRuleDialog('templateAdmin')"
                >{{ selectedTemplateAdminLabel }}</button>
              </div>
            </fieldset>

            <label class="basic-status-toggle"><input v-model="currentTemplate.isActive" type="checkbox" /> 启用模板</label>

            <div class="step-actions">
              <button class="secondary-btn" type="button" @click="saveTemplateConfig">保存</button>
              <button class="primary-btn" type="button" @click="switchEditorStep('form')">下一步</button>
            </div>
          </section>
        </div>

        <div v-else-if="editorStep === 'form'" class="form-designer-shell">
          <aside class="control-workbench">
            <nav class="control-rail" aria-label="表单设计导航">
              <button
                v-for="pane in controlPaneTabs"
                :key="pane.value"
                type="button"
                :aria-selected="activeControlPane === pane.value"
                @click="activeControlPane = pane.value"
              >
                <component :is="pane.icon" aria-hidden="true" />
                <span>{{ pane.label }}</span>
              </button>
            </nav>
            <section class="control-palette" aria-label="控件库">
              <h2>{{ activeControlPaneLabel }}</h2>
              <div
                v-for="category in paletteControlCategories"
                :key="category.key"
                class="control-category"
                :class="{ collapsed: !isControlCategoryExpanded(category.key) }"
              >
                <button class="control-category-header" type="button" @click="toggleControlCategory(category.key)">
                  <strong>{{ category.title }}</strong>
                  <component :is="isControlCategoryExpanded(category.key) ? ArrowDown : ArrowRight" aria-hidden="true" />
                </button>
                <div v-show="isControlCategoryExpanded(category.key)" class="control-card-grid">
                  <button
                    v-for="control in category.controls"
                    :key="control.key"
                    type="button"
                    class="control-card"
                    :class="{ disabled: isControlDisabled(control) }"
                    :disabled="isControlDisabled(control)"
                    :title="controlDisabledReason(control)"
                    draggable="true"
                    @click="addControl(control)"
                    @dragstart="onControlDragStart(control, $event)"
                    @dragend="onControlDragEnd"
                  >
                    <span class="control-card-icon" aria-hidden="true">
                      <component v-if="controlIconComponent(control)" :is="controlIconComponent(control)" />
                      <span v-else>{{ control.icon }}</span>
                    </span>
                    <span>{{ control.label }}</span>
                    <em v-if="control.badge">{{ control.badge }}</em>
                  </button>
                </div>
                <p v-if="category.note && isControlCategoryExpanded(category.key)" class="control-category-note">{{ category.note }}</p>
              </div>
            </section>
          </aside>

          <main class="form-designer-stage" @dragover.prevent @drop.prevent="onPreviewDrop">
            <div class="form-designer-toolbar">
              <div class="device-switch" role="group" aria-label="预览设备">
                <button type="button" :aria-pressed="formPreviewDevice === 'desktop'" @click="formPreviewDevice = 'desktop'">
                  <component :is="DataBoard" aria-hidden="true" />
                </button>
                <button type="button" :aria-pressed="formPreviewDevice === 'mobile'" @click="formPreviewDevice = 'mobile'">
                  <component :is="IphoneIcon" aria-hidden="true" />
                </button>
              </div>
              <div class="history-actions" aria-label="编辑历史">
                <button type="button" :disabled="!canUndoFormEdit" @click="undoFormEdit">↶</button>
                <button type="button" :disabled="!canRedoFormEdit" @click="redoFormEdit">↷</button>
              </div>
              <button class="preview-feature-btn" type="button" @click="openApprovalPreview">
                预览表单
              </button>
            </div>
            <aside class="phone-preview" :class="`phone-preview--${formPreviewDevice}`">
            <div class="phone-title">{{ currentTemplate.name }}</div>
            <div v-if="!designerPreviewFields.length" class="phone-empty">
              <div class="empty-doc"></div>
              <p>请添加控件来创建你需要的模板</p>
            </div>
            <div v-else class="phone-form" :class="{ overtime: currentTemplate.name.includes('加班'), leave: !currentTemplate.name.includes('加班') }">
              <div v-if="currentTemplate.name.includes('加班')" class="phone-tip">仅限法定节假日使用该申请</div>
              <div
                v-for="field in designerPreviewFields"
                :key="field.code"
                class="phone-line-row"
                :class="{
                  active: selectedFieldCode === field.code,
                  dragging: draggedFieldCode === field.code,
                  'drag-over': dragOverFieldCode === field.code,
                }"
              >
                <div
                  class="phone-line"
                  :class="{
                    active: selectedFieldCode === field.code,
                    'phone-line--compound': field.field_type === 'duration',
                    'phone-line--block': ['layout_column', 'attachment', 'image', 'signature', 'detail', 'static_text', 'phone'].includes(field.field_type),
                    'phone-line--layout': field.field_type === 'layout_column',
                  }"
                  role="button"
                  tabindex="0"
                  draggable="true"
                  @click="selectField(field.code)"
                  @keydown.enter.prevent="selectField(field.code)"
                  @keydown.space.prevent="selectField(field.code)"
                  @dragstart="onFieldDragStart(field.code, $event)"
                  @dragover.prevent="onFieldDragOver(field.code)"
                  @drop.prevent="onFieldDrop(field.code)"
                  @dragend="onFieldDragEnd"
                >
                  <template v-if="field.field_type === 'layout_column'">
                    <div class="phone-layout-preview">
                      <span></span>
                      <span></span>
                    </div>
                  </template>
                  <template v-else-if="field.field_type === 'duration'">
                    <div class="phone-compound-lines">
                      <div><b><span>开始时间</span> <i v-if="field.is_required">*</i></b><span>请选择</span></div>
                      <div><b><span>结束时间</span> <i v-if="field.is_required">*</i></b><span>请选择</span></div>
                      <div><b><span>{{ field.label }}</span> <i v-if="field.is_required">*</i></b><span>{{ durationPreviewValue(field) }}</span></div>
                    </div>
                  </template>
                  <template v-else-if="['attachment', 'image', 'signature'].includes(field.field_type)">
                    <div class="phone-attachment-preview">
                      <b><span>{{ field.label }}</span> <i v-if="field.is_required">*</i></b>
                      <button v-if="field.field_type === 'attachment'" class="attachment-add-button" type="button" @click.stop="showDesignerPreviewOnlyHint('附件上传')">
                        添加附件
                      </button>
                      <span v-else class="attachment-tile">⌁</span>
                    </div>
                  </template>
                  <template v-else-if="field.field_type === 'detail'">
                    <div class="phone-detail-preview">
                      <div class="detail-card-preview">
                        <header>
                          <b>{{ field.label }}</b>
                          <button type="button" @click.stop="duplicateField(field.code)">复制</button>
                        </header>
                        <div v-if="detailChildFields(field).length" class="detail-child-form">
                          <div
                            v-for="child in detailChildFields(field)"
                            :key="child.code"
                            class="detail-child-form-row"
                            :class="{ active: isDetailChildSelected(field.code, child.code) }"
                            role="button"
                            tabindex="0"
                            @click.stop="selectDetailChild(field.code, child.code)"
                            @keydown.enter.stop.prevent="selectDetailChild(field.code, child.code)"
                            @keydown.space.stop.prevent="selectDetailChild(field.code, child.code)"
                          >
                            <span><i v-if="child.is_required">*</i>{{ child.label }}</span>
                            <em>{{ previewPlaceholder(child) }}</em>
                            <button
                              class="detail-child-inline-remove"
                              type="button"
                              aria-label="删除子控件"
                              @click.stop="removeDetailChildField(field.code, child.code)"
                            >×</button>
                          </div>
                        </div>
                        <span v-else>可添加多个控件(不包含明细控件)</span>
                      </div>
                      <button class="detail-add-child-btn" type="button" aria-label="添加子控件" @click.stop="openDetailControlLibrary(field.code)">＋ 添加明细</button>
                      <div v-if="detailSummaryEnabled(field)" class="detail-summary-preview">
                        <span>{{ detailSummaryLabel(field) }}</span>
                        <em>自动计算结果</em>
                      </div>
                    </div>
                  </template>
                  <template v-else-if="field.field_type === 'static_text'">
                    <div class="phone-static-preview">{{ staticTextContent(field) || '请输入说明文字' }}</div>
                  </template>
                  <template v-else-if="field.field_type === 'phone'">
                    <div class="phone-number-preview">
                      <b><span>{{ field.label }}</span> <i v-if="field.is_required">*</i></b>
                      <span><em>{{ controlOption(field, 'country_code', '+86') }}</em><small></small>{{ field.placeholder || '请输入' }}</span>
                    </div>
                  </template>
                  <template v-else>
                    <b><span>{{ field.label }}</span> <i v-if="field.is_required">*</i></b>
                    <span>{{ previewPlaceholder(field) }}</span>
                  </template>
                </div>
                <div v-if="selectedFieldCode === field.code" class="phone-field-actions" aria-label="控件操作">
                  <button type="button" aria-label="复制控件" title="复制控件" @click.stop="duplicateField(field.code)">
                    <component :is="CopyDocument" aria-hidden="true" />
                  </button>
                  <button type="button" aria-label="删除控件" title="删除控件" @click.stop="removeField(field.code)">
                    <component :is="Delete" aria-hidden="true" />
                  </button>
                </div>
              </div>
            </div>
            <div class="phone-control-actions">
              <div class="phone-control-drop-hint" aria-label="控件添加提示">
                <component :is="Operation" aria-hidden="true" />
                <span>从左侧控件栏选择或拖拽控件；已添加控件可拖动排序</span>
              </div>
            </div>
            </aside>
          </main>

          <section class="config-panel designer-config-panel">
            <template v-if="selectedField">
              <header class="field-config-heading">
                <span class="field-config-icon" aria-hidden="true">
                  <component v-if="selectedFieldIconComponent" :is="selectedFieldIconComponent" />
                  <span v-else>{{ selectedFieldIconText }}</span>
                </span>
                <h2>{{ selectedDetailChildCode ? '子控件设置' : selectedFieldTypeName }}</h2>
              </header>

              <div class="field-config-body">
                <label class="field-config-line">
                  <span>标题</span>
                  <span class="field-input-with-count">
                    <input v-model="selectedField.label" maxlength="50" aria-label="控件名称" />
                    <small>{{ selectedField.label.length }} / 50</small>
                  </span>
                </label>
                <p v-if="selectedFieldLabelDuplicated" class="config-error">不能设置重复的控件名称</p>

                <label v-if="selectedField.field_type !== 'static_text'" class="field-config-line">
                  <span>提示文字</span>
                  <span class="field-input-with-count">
                    <input v-model="selectedField.placeholder" maxlength="50" aria-label="填写提示" />
                    <small>{{ String(selectedField.placeholder || '').length }} / 50</small>
                  </span>
                </label>
                <label v-else class="field-config-line field-config-line--textarea">
                  <span>说明文字</span>
                  <textarea :value="staticTextContent(selectedField)" maxlength="500" aria-label="说明文字" @input="onStaticTextInput"></textarea>
                </label>
                <button v-if="selectedField.field_type === 'static_text'" class="designer-text-link" type="button" @click="insertStaticTextLink">插入链接</button>

                <section v-if="fieldSupportsOptions(selectedField)" class="field-config-section">
                  <h3>选项</h3>
                  <div class="field-option-list">
                    <div
                      v-for="(option, optionIndex) in normalizedFieldOptions(selectedField)"
                      :key="`${selectedField.code}-${optionIndex}`"
                      class="field-option-row"
                    >
                      <button
                        class="field-option-default"
                        :class="{ active: controlOption(selectedField, 'default_option', '') === option }"
                        type="button"
                        :aria-label="`设为默认选项：${option || optionIndex + 1}`"
                        @click="setSelectedControlOption('default_option', option)"
                      ></button>
                      <span class="field-option-drag" aria-label="拖动排序">⋮⋮</span>
                      <input :value="option" :aria-label="`选项${optionIndex + 1}`" @input="updateSelectedOption(optionIndex, $event)" />
                      <button class="field-option-icon-btn" type="button" aria-label="设置选项图片" @click="showOptionImageHint">
                        <component :is="Collection" aria-hidden="true" />
                      </button>
                      <button class="field-option-icon-btn" type="button" aria-label="删除选项" @click="removeSelectedOption(optionIndex)">
                        <component :is="Delete" aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                  <div class="field-option-links">
                    <button type="button" @click="bulkEditSelectedOptions">批量编辑</button>
                    <button type="button" @click="addSelectedOption">添加选项</button>
                    <button type="button" @click="addOtherSelectedOption">添加其它项</button>
                    <button type="button" @click="showOptionRelationHint">选项关联</button>
                  </div>
                  <label class="designer-switch-row designer-switch-row--compact">
                    <span>选项平铺</span>
                    <span class="designer-switch">
                      <input
                        type="checkbox"
                        :checked="Boolean(controlOption(selectedField, 'flatten_options', false))"
                        aria-label="选项平铺"
                        @change="setSelectedBooleanControlOption('flatten_options', $event)"
                      />
                      <i></i>
                    </span>
                  </label>
                </section>

                <section v-else class="field-config-section">
                  <h3>默认值</h3>
                  <label class="designer-radio-line">
                    <input type="radio" checked />
                    <span>自定义</span>
                  </label>
                  <p class="designer-feature-tip">以下功能属于表单增强功能 <button type="button" @click="showEnhancedFeatureHint">立即开通</button></p>
                  <label class="designer-radio-line disabled">
                    <input type="radio" disabled />
                    <span>数据联动</span>
                  </label>
                  <label class="designer-radio-line disabled">
                    <input type="radio" disabled />
                    <span>公式编辑</span>
                  </label>
                  <input
                    class="field-default-input"
                    :value="controlOption(selectedField, 'default_value', '')"
                    aria-label="默认值"
                    @input="setSelectedControlOptionFromEvent('default_value', $event)"
                  />
                </section>

                <section class="field-config-section">
                  <h3>填写校验 <small>填写表单时校验内容，并给出提示</small></h3>
                  <button class="field-validate-btn" type="button" @click="showValidationHint">添加校验条件</button>
                </section>

                <div class="designer-switch-list">
                  <label class="designer-switch-row" :class="{ disabled: !selectedFieldSupportsRequired(selectedField) }">
                    <span>必填</span>
                    <span class="designer-switch">
                      <input
                        v-model="selectedField.is_required"
                        type="checkbox"
                        aria-label="必填"
                        :disabled="!selectedFieldSupportsRequired(selectedField)"
                      />
                      <i></i>
                    </span>
                  </label>
                  <label class="designer-switch-row">
                    <span>扫码 <small>用于移动端扫码录入</small></span>
                    <span class="designer-switch">
                      <input
                        type="checkbox"
                        :checked="Boolean(controlOption(selectedField, 'scan_enabled', false))"
                        aria-label="扫码"
                        @change="setSelectedBooleanControlOption('scan_enabled', $event)"
                      />
                      <i></i>
                    </span>
                  </label>
                  <label class="designer-switch-row" :class="{ disabled: !selectedFieldSupportsPrint(selectedField) }">
                    <span>参与打印 <small>如不勾选，打印时不显示该项</small></span>
                    <span class="designer-switch">
                      <input
                        type="checkbox"
                        :checked="selectedField.printable !== false"
                        aria-label="参与打印"
                        :disabled="!selectedFieldSupportsPrint(selectedField)"
                        @change="setSelectedFieldBoolean('printable', $event)"
                      />
                      <i></i>
                    </span>
                  </label>
                </div>

                <div class="field-advanced-block">
                  <div v-if="selectedFieldSupportsSelectionMode" class="control-extra-row">
                    <span>选择方式</span>
                    <label><input type="radio" :checked="controlOption(selectedField, 'selection_mode', 'multiple') === 'multiple'" @change="setSelectedControlOption('selection_mode', 'multiple')" /> 多选</label>
                    <label><input type="radio" :checked="controlOption(selectedField, 'selection_mode', 'multiple') === 'single'" @change="setSelectedControlOption('selection_mode', 'single')" /> 单选</label>
                  </div>
                  <div v-if="fieldSupportsLocationRange(selectedField)" class="control-extra-row">
                    <span>选择范围</span>
                    <div class="inline-control">
                      <span>申请人可以选</span>
                      <select :value="controlOption(selectedField, 'range_meters', 300)" @change="setSelectedNumericControlOption('range_meters', $event)">
                        <option :value="100">100米</option>
                        <option :value="200">200米</option>
                        <option :value="300">300米</option>
                      </select>
                      <span>之内的范围</span>
                    </div>
                  </div>
                  <div v-if="fieldSupportsDurationRules(selectedField)" class="duration-config-box">
                    <label>
                      <span>时间刻度</span>
                      <select :value="controlOption(selectedField, 'time_scale', 'day')" @change="setSelectedControlOptionFromEvent('time_scale', $event)">
                        <option value="day">按天</option>
                        <option value="hour">按小时</option>
                      </select>
                    </label>
                    <p>{{ durationScaleHint(selectedField) }}</p>
                    <label>
                      <span>单位换算</span>
                      <span class="unit-conversion">1天 = <input :value="controlOption(selectedField, 'unit_hours', 24)" inputmode="decimal" @input="setSelectedNumericControlOption('unit_hours', $event)" /> 小时</span>
                    </label>
                    <label>
                      <span>时长计算</span>
                      <select :value="controlOption(selectedField, 'duration_mode', 'natural_day')" @change="setSelectedControlOptionFromEvent('duration_mode', $event)">
                        <option value="workday">工作日</option>
                        <option value="natural_day">自然日</option>
                      </select>
                    </label>
                    <p>{{ controlOption(selectedField, 'duration_mode', 'natural_day') === 'workday' ? '时长按工作日计算' : '时长按自然日计算' }}</p>
                  </div>
                  <div v-if="selectedField.field_type === 'attachment'" class="control-extra-row">
                    <span>控件限制</span>
                    <label><input type="checkbox" :checked="Boolean(controlOption(selectedField, 'mobile_photo_only', false))" @change="setSelectedBooleanControlOption('mobile_photo_only', $event)" /> 成员仅可在手机端拍照上传</label>
                  </div>
                  <div v-if="selectedField.field_type === 'related_approval'" class="related-approval-config">
                    <div class="control-extra-row">
                      <span>可关联审批单类型</span>
                      <label><input type="radio" :checked="controlOption(selectedField, 'relation_scope', 'all') === 'all'" @change="setSelectedControlOption('relation_scope', 'all')" /> 全部类型申请单</label>
                      <label><input type="radio" :checked="controlOption(selectedField, 'relation_scope', 'all') === 'specified'" @change="setSelectedControlOption('relation_scope', 'specified')" /> 指定类型申请单</label>
                    </div>
                    <div v-if="controlOption(selectedField, 'relation_scope', 'all') === 'specified'" class="related-template-grid">
                      <button
                        v-for="template in relatedApprovalTemplateOptions"
                        :key="template.code"
                        type="button"
                        :class="{ selected: selectedRelatedApprovalCodes.includes(template.code) }"
                        @click="toggleRelatedApprovalCode(template.code)"
                      >{{ template.name }}</button>
                    </div>
                  </div>
                  <div v-if="selectedField.field_type === 'detail'" class="detail-print-config">
                    <div class="control-extra-row">
                      <span>自动合计</span>
                      <label><input type="checkbox" :checked="detailSummaryEnabled(selectedField)" @change="setSelectedDetailSummaryEnabled($event)" /> 移动端自动计算明细合计</label>
                    </div>
                    <label v-if="detailSummaryEnabled(selectedField)">
                      <span>合计标题</span>
                      <input :value="detailSummaryLabel(selectedField)" aria-label="明细合计标题" @input="setSelectedDetailSummaryLabel" />
                    </label>
                    <div v-if="detailSummaryEnabled(selectedField)" class="control-extra-row detail-summary-fields">
                      <span>合计字段</span>
                      <template v-if="numericDetailChildFields(selectedField).length">
                        <label v-for="child in numericDetailChildFields(selectedField)" :key="child.code">
                          <input type="checkbox" :checked="detailSummaryUsesField(selectedField, child.code)" @change="toggleSelectedDetailSummaryField(child.code)" />
                          {{ child.label }}
                        </label>
                      </template>
                      <p v-else>添加金额或数字子控件后，移动端会按这些字段求和。</p>
                    </div>
                    <div class="control-extra-row">
                      <span>打印格式</span>
                      <label><input type="radio" :checked="controlOption(selectedField, 'print_layout', 'single_line') === 'single_line'" @change="setSelectedControlOption('print_layout', 'single_line')" /> 合并成一行打印</label>
                      <label><input type="radio" :checked="controlOption(selectedField, 'print_layout', 'single_line') === 'multi_line'" @change="setSelectedControlOption('print_layout', 'multi_line')" /> 拆分成多行打印</label>
                    </div>
                    <div class="detail-print-preview" :class="String(controlOption(selectedField, 'print_layout', 'single_line'))">
                      <span>标题</span><span>标题</span><span>标题</span><span>标题</span>
                      <span>明细1</span><i></i><i></i><i></i>
                      <span>明细1</span><i></i><i></i><i></i>
                    </div>
                  </div>
                  <div v-if="selectedField.field_type === 'phone'" class="control-extra-row">
                    <span>默认区号</span>
                    <select :value="controlOption(selectedField, 'country_code', '+86')" @change="setSelectedControlOptionFromEvent('country_code', $event)">
                      <option value="+86">+86 中国大陆</option>
                      <option value="+852">+852 中国香港</option>
                      <option value="+853">+853 中国澳门</option>
                      <option value="+886">+886 中国台湾</option>
                    </select>
                  </div>
                  <p v-if="attendanceComponentHint(selectedField)" class="hint-box">{{ attendanceComponentHint(selectedField) }}</p>
                  <div v-if="fieldShowsLeaveTypeTable(selectedField)" class="leave-type-table">
                    <div>类型名称</div><div>时间刻度</div><div>时长计算</div>
                    <span>年假</span><span>按天</span><span>工作日</span>
                    <span>婚假</span><span>按天</span><span>自然日</span>
                    <span>产休假</span><span>按小时</span><span>自然日</span>
                    <span>陪产假</span><span>按天</span><span>自然日</span>
                    <span>病假</span><span>按天</span><span>自然日</span>
                    <span>调休</span><span>按小时</span><span>工作日</span>
                  </div>
                  <p v-if="fieldShowsLeaveTypeTable(selectedField)" class="hint-box">提示：选择请假类型和起止时间后，自动计算请假时长并在假期余额中扣除。</p>
                </div>

                <button class="primary-btn save-btn designer-save-btn" data-testid="save-template-btn" type="button" @click="saveTemplateConfig">保存</button>
              </div>
            </template>

            <template v-else>
              <header class="field-config-heading">
                <span class="field-config-icon template-icon" :class="currentTemplateColor">
                  <component :is="currentTemplateIcon" />
                </span>
                <h2>模板设置</h2>
              </header>
              <div class="field-config-body">
                <label class="field-config-line">
                  <span>模板名称</span>
                  <span class="field-input-with-count">
                    <input v-model="currentTemplate.name" maxlength="50" aria-label="模板名称" />
                    <small>{{ currentTemplate.name.length }} / 50</small>
                  </span>
                </label>
                <label class="field-config-line">
                  <span>模板分组</span>
                  <select v-model="currentTemplate.group">
                    <option v-for="groupName in templateGroupOptions" :key="groupName" :value="groupName">{{ groupName }}</option>
                  </select>
                </label>
                <div class="radio-row"><span>打印格式</span><label><input type="radio" checked /> 默认格式</label><label><input type="radio" /> 自定义格式</label></div>
                <button class="primary-btn save-btn designer-save-btn" data-testid="save-template-btn" type="button" @click="saveTemplateConfig">保存</button>
              </div>
            </template>
          </section>
        </div>

        <div v-else-if="editorStep === 'flow'" class="flow-designer-view">
          <div class="flow-designer-toolbar">
            <div class="flow-version-control">
              <button
                class="flow-version-trigger"
                :class="{ 'flow-version-trigger--draft': currentFlowVersionMode === 'draft' }"
                type="button"
                @click="flowVersionMenuVisible = !flowVersionMenuVisible"
              >
                <strong>{{ flowVersionTriggerLabel }}</strong>
                <b :class="`flow-version-status--${flowVersionStatusTone}`">{{ flowVersionStatusLabel }}</b>
                <span aria-hidden="true">⌄</span>
              </button>
              <span class="flow-save-state">{{ flowVersionMetaText }}</span>
              <div v-if="flowVersionMenuVisible" class="flow-version-menu" role="menu">
                <button
                  class="flow-version-option"
                  :class="{ active: currentFlowVersionMode === 'draft' }"
                  type="button"
                  role="menuitem"
                  @click="selectDraftFlowVersion"
                >
                  <span>
                    <strong>未发布版本 <b class="flow-version-status--draft">设计中</b></strong>
                    <small>{{ draftVersionMetaText }}</small>
                  </span>
                  <i v-if="currentFlowVersionMode === 'draft'" aria-hidden="true">✓</i>
                </button>
                <button
                  v-for="version in visibleFlowVersions"
                  :key="version.id"
                  class="flow-version-option"
                  :class="{ active: currentFlowVersionMode === 'published' && selectedFlowVersionId === version.id }"
                  type="button"
                  role="menuitem"
                  @click="selectPublishedFlowVersion(version)"
                >
                  <span>
                    <strong>流程版本V{{ version.version }} <b class="flow-version-status--active">{{ versionStatusLabel(version) }}</b></strong>
                    <small>{{ formatFlowVersionTime(version.published_at) }}发布</small>
                  </span>
                  <i v-if="currentFlowVersionMode === 'published' && selectedFlowVersionId === version.id" aria-hidden="true">✓</i>
                </button>
              </div>
            </div>
            <div class="flow-workbench-actions">
              <button class="secondary-btn" type="button" @click="openApprovalPreview">预览</button>
              <button class="secondary-btn" type="button" @click="openSimulationDialog">模拟提交</button>
              <button class="primary-btn" type="button" @click="saveTemplateConfig">发布</button>
            </div>
          </div>

          <div class="flow-canvas-shell">
            <div class="flow-zoom-control" aria-label="流程画布缩放">
              <button type="button" @click="changeFlowZoom(-10)">−</button>
              <button type="button" @click="resetFlowZoom">{{ flowZoom }}%</button>
              <button type="button" @click="changeFlowZoom(10)">+</button>
            </div>
            <FlowDesignerCanvas
              :nodes="flowNodes"
              :selected-node-uid="selectedFlowNodeUid"
              :template-name="currentTemplate.name"
              :zoom="flowZoom"
              :member-names="memberNameMap"
              :condition-field-labels="conditionFieldLabelMap"
              :company-names="companyNameMap"
              :department-names="departmentNameMap"
              @select-node="selectFlowNode"
              @select-branch="selectFlowBranch"
              @copy-branch="copyConditionBranch"
              @add-branch="addConditionBranchFromCanvas"
              @insert-node="insertFlowNodeFromCanvas"
              @remove-node="removeFlowNode"
              @remove-branch="removeConditionBranchFromCanvas"
              @select-applicant="flowConfigDrawerVisible = false"
              @add-automation="addAutomationNode"
            />
          </div>

          <div v-if="flowConfigDrawerVisible" class="flow-drawer-backdrop" @click="closeFlowConfigDrawer"></div>
          <section class="flow-side-panel flow-config-drawer" v-if="flowConfigDrawerVisible && selectedFlowNode">
              <header :class="{ 'flow-drawer-header--condition': isSplitFlowNode(selectedFlowNode) }">
                <template v-if="isSplitFlowNode(selectedFlowNode) && activeBranch">
                  <h3 class="condition-drawer-title">
                    <input v-model="activeBranch.label" :aria-label="`${activeBranch.label || '分支'}名称`" :placeholder="isParallelBranchNode(selectedFlowNode) ? '分支' : '条件'" @input="markRulesDirty" />
                    <span aria-hidden="true">✎</span>
                  </h3>
                  <div class="flow-drawer-actions">
                    <select
                      v-if="isConditionBranchNode(selectedFlowNode) && !activeBranch.is_default_branch"
                      class="branch-priority-select"
                      :value="activeBranchPriority"
                      aria-label="条件优先级"
                      @change="setBranchPriority(activeBranch.uid, $event)"
                    >
                      <option v-for="(_, index) in selectedFlowNode.branches || []" :key="index" :value="index + 1">优先级{{ index + 1 }}</option>
                    </select>
                    <span class="condition-info-icon" aria-hidden="true">i</span>
                    <button class="node-open-btn" type="button" aria-label="关闭节点设置" @click="closeFlowConfigDrawer">×</button>
                  </div>
                </template>
                <template v-else>
                  <h3>{{ nodeDialogTitle(selectedFlowNode) }}</h3>
                <div class="flow-drawer-actions">
                  <button class="link-btn" type="button" @click="removeFlowNode(selectedFlowNode.uid)">删除节点</button>
                  <button class="node-open-btn" type="button" aria-label="关闭节点设置" @click="closeFlowConfigDrawer">×</button>
                </div>
                </template>
              </header>

              <section v-if="!isSplitFlowNode(selectedFlowNode)" class="node-config-chrome">
                <div v-if="isApprovalLikeNode(selectedFlowNode) && selectedFlowNode.node_type !== 'handler'" class="node-config-block node-config-block--approval-type">
                  <h4>审批类型</h4>
                  <div class="node-radio-line">
                    <label><input type="radio" :checked="approvalExecutionType(selectedFlowNode) === 'manual'" @change="setApprovalExecutionType(selectedFlowNode, 'manual')" /><span>人工审批</span></label>
                    <label><input type="radio" :checked="approvalExecutionType(selectedFlowNode) === 'auto_approve'" @change="setApprovalExecutionType(selectedFlowNode, 'auto_approve')" /><span>自动通过</span></label>
                    <label><input type="radio" :checked="approvalExecutionType(selectedFlowNode) === 'auto_reject'" @change="setApprovalExecutionType(selectedFlowNode, 'auto_reject')" /><span>自动拒绝</span></label>
                  </div>
                </div>
                <nav class="node-config-tabs" aria-label="节点设置分页">
                  <button type="button" :aria-selected="nodeConfigTab === 'assignee'" @click="nodeConfigTab = 'assignee'">
                    {{ selectedFlowNode.node_type === 'notify' ? '设置抄送人' : '设置审批人' }}
                  </button>
                  <button type="button" :aria-selected="nodeConfigTab === 'permissions'" @click="nodeConfigTab = 'permissions'">表单操作权限</button>
                </nav>
              </section>

              <section v-if="!isSplitFlowNode(selectedFlowNode) && nodeConfigTab === 'assignee'" class="node-config-section">
                <template v-if="selectedFlowNode.node_type === 'notify'">
                  <h4>请选择该节点的抄送人</h4>
                  <div class="assignee-option-card assignee-option-card--notify">
                    <div class="assignee-option-group">
                      <strong>常用抄送人</strong>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'specific_user') }" @click="toggleNotifySource(selectedFlowNode, 'specific_user')"><span></span>指定成员</button>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'role') }" @click="toggleNotifySource(selectedFlowNode, 'role')"><span></span>角色</button>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'applicant_self') }" @click="toggleNotifySource(selectedFlowNode, 'applicant_self')"><span></span>发起人自己</button>
                    </div>
                    <div class="assignee-option-group">
                      <strong>主管</strong>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'direct_manager') }" @click="toggleNotifySource(selectedFlowNode, 'direct_manager')"><span></span>直属主管</button>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'department_head') }" @click="toggleNotifySource(selectedFlowNode, 'department_head')"><span></span>部门主管</button>
                    </div>
                    <div class="assignee-option-group">
                      <strong>其他</strong>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'form_department_head'), disabled: sourceUnavailableReason('form_department_head') }" @click="toggleNotifySource(selectedFlowNode, 'form_department_head')"><span :class="{ 'blocked-option-icon': sourceUnavailableReason('form_department_head') }">{{ sourceUnavailableReason('form_department_head') ? '⊘' : '' }}</span>表单内部门控件</button>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'related_member_field'), disabled: sourceUnavailableReason('related_member_field') }" @click="toggleNotifySource(selectedFlowNode, 'related_member_field')"><span :class="{ 'blocked-option-icon': sourceUnavailableReason('related_member_field') }">{{ sourceUnavailableReason('related_member_field') ? '⊘' : '' }}</span>表单内的联系人</button>
                      <button type="button" class="choice-option" :class="{ selected: isNotifySourceSelected(selectedFlowNode, 'applicant_select') }" @click="toggleNotifySource(selectedFlowNode, 'applicant_select')"><span></span>发起人自选</button>
                      <button v-for="item in unsupportedNotifyOptions" :key="item.label" type="button" class="choice-option disabled" @click="showDisabledNodeOption(item.reason)"><span class="blocked-option-icon">⊘</span>{{ item.label }}<em v-if="item.trial">试用</em></button>
                    </div>
                  </div>

                  <label v-if="isNotifySourceSelected(selectedFlowNode, 'specific_user')" class="node-inline-field">
                    <span>指定成员</span>
                    <div class="member-picker-inline">
                      <input :value="memberNamesForFlowNode(selectedFlowNode)" readonly placeholder="请选择，可多选" />
                      <button class="secondary-btn" type="button" @click="openMemberPicker(selectedFlowNode)">选择</button>
                    </div>
                  </label>
                  <label v-if="isNotifySourceSelected(selectedFlowNode, 'role')" class="node-inline-field">
                    <span>角色</span>
                    <select v-model="selectedFlowNode.role" @change="markRulesDirty">
                      <option v-for="role in approvalRoleOptions" :key="role.value" :value="role.value">{{ role.label }}</option>
                    </select>
                  </label>
                  <label v-if="isNotifySourceSelected(selectedFlowNode, 'related_member_field')" class="node-inline-field">
                    <span>联系人控件</span>
                    <select :value="selectedFlowNode.related_member_field_code" @change="setRelatedMemberField(selectedFlowNode, $event)">
                      <option value="">请选择成员控件</option>
                      <option v-for="field in memberFieldOptions" :key="field.code" :value="field.code">{{ field.label }}</option>
                    </select>
                  </label>
                  <label v-if="isNotifySourceSelected(selectedFlowNode, 'form_department_head')" class="node-inline-field">
                    <span>部门控件</span>
                    <select :value="selectedFlowNode.related_department_field_code" @change="setRelatedDepartmentField(selectedFlowNode, $event)">
                      <option value="">请选择部门控件</option>
                      <option v-for="field in departmentFieldOptions" :key="field.code" :value="field.code">{{ field.label }}</option>
                    </select>
                  </label>
                </template>

                <template v-else-if="approvalExecutionType(selectedFlowNode) === 'manual'">
                  <h4>请选择该节点的审批人</h4>
                  <div class="assignee-option-card assignee-option-card--approval">
                    <div class="assignee-option-group">
                      <strong>常用审批人</strong>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'specific_user') }" @click="selectAssigneeSource(selectedFlowNode, 'specific_user')"><span></span>指定成员</button>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'applicant_self') }" @click="selectAssigneeSource(selectedFlowNode, 'applicant_self')"><span></span>发起人自己</button>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'applicant_select') }" @click="selectAssigneeSource(selectedFlowNode, 'applicant_select')"><span></span>发起人自选</button>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'role') }" @click="selectAssigneeSource(selectedFlowNode, 'role')"><span></span>角色</button>
                    </div>
                    <div class="assignee-option-group">
                      <strong>主管</strong>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'direct_manager') }" @click="selectAssigneeSource(selectedFlowNode, 'direct_manager')"><span></span>直属主管</button>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'department_head') }" @click="selectAssigneeSource(selectedFlowNode, 'department_head')"><span></span>部门主管</button>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'multi_level_manager') }" @click="selectAssigneeSource(selectedFlowNode, 'multi_level_manager')"><span></span>连续多级主管</button>
                    </div>
                    <div class="assignee-option-group">
                      <strong>其他</strong>
                      <button type="button" class="choice-option disabled" @click="showDisabledNodeOption('行业通讯录部门对应主管尚未接入审批运行规则，暂不可选')"><span class="blocked-option-icon">⊘</span>行业通讯录部门对应主管</button>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'form_department_head'), disabled: sourceUnavailableReason('form_department_head') }" @click="selectAssigneeSource(selectedFlowNode, 'form_department_head')"><span :class="{ 'blocked-option-icon': sourceUnavailableReason('form_department_head') }">{{ sourceUnavailableReason('form_department_head') ? '⊘' : '' }}</span>表单内部门控件</button>
                      <button type="button" class="choice-option" :class="{ selected: isSourceSelected(selectedFlowNode, 'related_member_field'), disabled: sourceUnavailableReason('related_member_field') }" @click="selectAssigneeSource(selectedFlowNode, 'related_member_field')"><span :class="{ 'blocked-option-icon': sourceUnavailableReason('related_member_field') }">{{ sourceUnavailableReason('related_member_field') ? '⊘' : '' }}</span>表单内的联系人</button>
                      <button type="button" class="choice-option disabled" @click="showDisabledNodeOption('连接器审批人源尚未接入审批运行规则，暂不可选')"><span class="blocked-option-icon">⊘</span>从连接器获取<em>试用</em></button>
                    </div>
                  </div>

                  <div v-if="selectedFlowNode.assignee_source === 'specific_user'" class="node-config-block node-assignee-detail">
                    <h4>指定成员 <small>可添加多个节点审批人，不能超过50人</small></h4>
                    <p class="muted">点击“添加成员”即可为当前审批节点配置一名或多名审批人，保存后会按节点回显。</p>
                    <div class="member-picker-bar">
                      <button class="secondary-btn" type="button" @click="openMemberPicker(selectedFlowNode)">＋ 添加成员</button>
                      <span v-if="flowNodeMemberIds(selectedFlowNode).length">{{ memberNamesForFlowNode(selectedFlowNode) }}</span>
                      <span v-else class="muted">尚未添加审批人</span>
                    </div>
                  </div>

                  <div v-else-if="selectedFlowNode.assignee_source === 'applicant_self'" class="node-config-block node-assignee-detail">
                    <h4>发起人自己</h4>
                    <p class="muted">发起人自己将作为审批人处理审批单</p>
                  </div>

                  <div v-else-if="selectedFlowNode.assignee_source === 'applicant_select'" class="node-config-block node-assignee-detail">
                    <h4>发起人自选</h4>
                    <div class="node-select-row">
                      <select v-model="selectedFlowNode.applicant_select_mode" @change="onApplicantSelectModeChange(selectedFlowNode)">
                        <option v-for="item in applicantSelectModeOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
                      </select>
                      <select v-model="selectedFlowNode.applicant_select_scope" @change="onApplicantSelectScopeChange(selectedFlowNode)">
                        <option v-for="item in applicantSelectScopeOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
                      </select>
                    </div>
                    <div v-if="selectedFlowNode.applicant_select_scope === 'selected_members'" class="member-picker-bar">
                      <button class="secondary-btn" type="button" @click="openMemberPicker(selectedFlowNode, 'applicant_select_scope')">＋ 添加成员</button>
                      <span v-if="selectedFlowNode.applicant_select_member_ids.length">{{ applicantSelectMemberNames(selectedFlowNode) }}</span>
                      <span v-else class="muted">请选择发起人可自选的候选成员</span>
                    </div>
                    <div v-else-if="selectedFlowNode.applicant_select_scope === 'role'" class="role-add-bar role-add-bar--picker">
                      <span class="role-selected-list">
                        <span v-if="selectedFlowNode.applicant_select_roles.length" class="role-chip">{{ applicantSelectRoleNames(selectedFlowNode) }}</span>
                        <span v-else class="muted">请选择发起人可自选的角色</span>
                      </span>
                      <button class="secondary-btn" type="button" @click="openRolePicker(selectedFlowNode, 'applicant_select_roles')">＋ 添加</button>
                    </div>
                  </div>

                  <div v-else-if="selectedFlowNode.assignee_source === 'role'" class="node-config-block node-assignee-detail">
                    <div class="node-block-heading">
                      <h4>添加角色</h4>
                      <span>发起人如何从角色中自选审批人 ?</span>
                    </div>
                    <div class="role-add-bar">
                      <span class="role-selected-list">
                        <span class="role-chip">{{ roleLabel(selectedFlowNode.role) }}</span>
                      </span>
                      <button class="secondary-btn" type="button" @click="openRolePicker(selectedFlowNode, 'role')">＋ 添加</button>
                    </div>
                  </div>

                  <div v-else-if="selectedFlowNode.assignee_source === 'direct_manager'" class="node-config-block node-assignee-detail">
                    <h4>直属主管</h4>
                    <label class="node-inline-field">
                      <span>发起人的</span>
                      <select v-model.number="selectedFlowNode.manager_level" @change="markRulesDirty">
                        <option v-for="item in managerLevelOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
                      </select>
                    </label>
                  </div>

                  <div v-else-if="selectedFlowNode.assignee_source === 'department_head'" class="node-config-block node-assignee-detail">
                    <h4>部门主管</h4>
                    <label class="node-inline-field">
                      <span>发起人的</span>
                      <select v-model.number="selectedFlowNode.manager_level" @change="markRulesDirty">
                        <option v-for="item in managerLevelOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
                      </select>
                    </label>
                    <div class="side-checks side-checks--plain">
                      <label><input v-model="selectedFlowNode.fallback_to_upper_manager" type="checkbox" @change="markRulesDirty" /> 找不到主管时，由上级主管代审批</label>
                      <label><input v-model="selectedFlowNode.applicant_self_if_head" type="checkbox" @change="markRulesDirty" /> 发起人是部门主管时由发起人审批</label>
                    </div>
                  </div>

                  <div v-else-if="selectedFlowNode.assignee_source === 'multi_level_manager'" class="node-config-block node-assignee-detail">
                    <h4>连续多级主管审批终点</h4>
                    <div class="node-radio-stack">
                      <label>
                        <input type="radio" :checked="selectedFlowNode.multi_level_end_type === 'member'" @change="selectMultiLevelMemberEndType(selectedFlowNode)" />
                        指定人员（同时是<span class="inline-link">主管线上的主管</span>）
                      </label>
                    </div>
                    <div v-if="selectedFlowNode.multi_level_end_type === 'member'" class="multi-level-subpanel">
                      <div class="member-picker-bar">
                        <span>
                          {{ multiLevelMemberNames(selectedFlowNode) || '请选择主管链上的人员' }}
                        </span>
                        <button class="secondary-btn" type="button" @click="openMemberPicker(selectedFlowNode, 'multi_level_member')">选择人员</button>
                      </div>
                      <label class="node-inline-check">
                        <input v-model="selectedFlowNode.multi_level_limit_enabled" type="checkbox" @change="markRulesDirty" />
                        <span>同时不超过发起人向上的</span>
                        <select v-model.number="selectedFlowNode.multi_level_limit_level" @change="markRulesDirty">
                          <option v-for="item in managerLevelOptions" :key="item.value" :value="item.value">第{{ item.value }}级主管</option>
                        </select>
                      </label>
                    </div>
                    <label class="node-inline-field multi-level-directory-row">
                      <span><input type="radio" :checked="selectedFlowNode.multi_level_end_type === 'level'" @change="setMultiLevelEndType(selectedFlowNode, 'level')" /> 通讯录中的</span>
                      <select v-model="selectedFlowNode.multi_level_level" @change="markRulesDirty">
                        <option v-for="item in multiLevelDirectoryOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
                      </select>
                    </label>
                  </div>

                  <label v-if="selectedFlowNode.assignee_source === 'related_member_field'" class="node-inline-field">
                    <span>联系人控件</span>
                    <select :value="selectedFlowNode.related_member_field_code" @change="setRelatedMemberField(selectedFlowNode, $event)">
                      <option value="">请选择成员控件</option>
                      <option v-for="field in memberFieldOptions" :key="field.code" :value="field.code">{{ field.label }}</option>
                    </select>
                  </label>
                  <label v-if="selectedFlowNode.assignee_source === 'form_department_head'" class="node-inline-field">
                    <span>部门控件</span>
                    <select :value="selectedFlowNode.related_department_field_code" @change="setRelatedDepartmentField(selectedFlowNode, $event)">
                      <option value="">请选择部门控件</option>
                      <option v-for="field in departmentFieldOptions" :key="field.code" :value="field.code">{{ field.label }}</option>
                    </select>
                  </label>

                  <div v-if="showApprovalModeOptions(selectedFlowNode)" class="node-config-block">
                    <h4>多人审批时采用的审批方式</h4>
                    <div class="node-radio-stack">
                      <label><input v-model="selectedFlowNode.approval_mode" type="radio" value="sequential" @change="markRulesDirty" /> 依次审批（按顺序同意或拒绝）</label>
                      <label><input v-model="selectedFlowNode.approval_mode" type="radio" value="counter_sign" @change="markRulesDirty" /> 会签（需要所有审批人都同意才可通过）</label>
                      <label><input v-model="selectedFlowNode.approval_mode" type="radio" value="or_sign" @change="markRulesDirty" /> 或签（其中一名审批人同意或拒绝即可）</label>
                      <label v-if="selectedFlowNode.assignee_source === 'department_head'"><input v-model="selectedFlowNode.approval_mode" type="radio" value="vote" @change="markRulesDirty" /> 投票（满足设定的同意人数即可通过） <button class="inline-link-button" type="button">查看详情</button></label>
                    </div>
                    <label v-if="selectedFlowNode.approval_mode === 'vote'" class="node-inline-field">
                      <span>通过人数</span>
                      <input v-model.number="selectedFlowNode.vote_pass_count" class="node-number-input" min="1" type="number" @input="markRulesDirty" />
                    </label>
                  </div>

                  <div v-if="showEmptyApproverOptions(selectedFlowNode)" class="node-config-block">
                    <h4>审批人为为空时</h4>
                    <div class="node-radio-stack">
                      <label v-for="item in approvalEmptyActionOptions" :key="item.value">
                        <input type="radio" :checked="selectedFlowNode.empty_action === item.value" @change="setNodeEmptyAction(selectedFlowNode, item.value)" />
                        {{ item.label }}
                      </label>
                    </div>
                    <label v-if="selectedFlowNode.empty_action === 'transfer_to_specific'" class="node-inline-field">
                      <span>指定人员</span>
                      <select v-model.number="selectedFlowNode.empty_member_id" @change="markRulesDirty">
                        <option :value="null">请选择</option>
                        <option v-for="member in memberOptions" :key="member.id" :value="member.id">{{ member.name }}</option>
                      </select>
                    </label>
                  </div>
                </template>
              </section>

              <section v-if="!isSplitFlowNode(selectedFlowNode) && nodeConfigTab === 'permissions'" class="node-config-section">
                <div class="side-checks">
                  <label v-if="selectedFlowNode.node_type === 'approval'"><input v-model="ruleSettings.approvalCommentRequired" type="checkbox" @change="markRulesDirty" /> 审批意见必填</label>
                  <label v-if="selectedFlowNode.node_type === 'handler'"><input v-model="ruleSettings.handlerCommentRequired" type="checkbox" @change="markRulesDirty" /> 办理意见必填</label>
                  <label><input v-model="ruleSettings.fixedApproverLocked" type="checkbox" @change="markRulesDirty" /> 固定人员不可修改</label>
                  <label><input v-model="ruleSettings.fixedCcLocked" type="checkbox" @change="markRulesDirty" /> 固定抄送人不可删除</label>
                </div>
              </section>

              <div v-if="isConditionBranchNode(selectedFlowNode)" class="branch-editor branch-editor--simple">
                <p v-if="activeBranch?.is_default_branch" class="branch-default-note">默认条件会承接其他未命中的申请，无需额外设置。</p>

                <div v-else-if="activeBranch" class="branch-panel">
                  <div class="branch-conditions branch-condition-builder branch-condition-builder--simple">
                    <template v-for="(conditionGroup, groupIndex) in activeBranch.condition_groups || []" :key="conditionGroup.uid">
                      <div v-if="groupIndex > 0" class="branch-group-separator">或</div>
                      <section class="branch-condition-group branch-condition-group--simple">
                        <template v-for="(condition, conditionIndex) in conditionGroup.conditions" :key="condition.uid">
                          <div v-if="conditionIndex > 0" class="branch-row-connector">且</div>
                          <div class="branch-condition-row branch-condition-row--builder branch-condition-row--simple">
                            <span class="branch-condition-label">{{ conditionFieldLabel(condition.field) }}</span>

                            <div v-if="conditionFieldKind(condition.field) === 'applicant'" class="condition-value-editor condition-scope-editor">
                              <button class="condition-scope-trigger" type="button" @click="openApplicantScopePicker(condition)">
                                <span v-for="item in conditionApplicantScopeValues(condition)" :key="applicantScopeKey(item)" class="condition-chip">
                                  {{ applicantScopeLabel(item) }}
                                </span>
                                <span v-if="!conditionApplicantScopeValues(condition).length" class="muted">请选择具体人员/角色/部门</span>
                              </button>
                            </div>

                            <div v-else-if="conditionFieldKind(condition.field) === 'number'" class="condition-value-editor condition-number-editor">
                              <select v-model="condition.operator" @change="onBranchConditionOperatorChange(condition)">
                                <option v-for="operator in conditionOperatorOptions(condition)" :key="operator.value" :value="operator.value">{{ operator.label }}</option>
                              </select>
                              <div v-if="condition.operator === 'between'" class="condition-between-inputs">
                                <input type="number" :value="conditionBetweenBound(condition, 0)" placeholder="最小值" @input="setConditionBetweenBound(condition, 0, ($event.target as HTMLInputElement).value)" />
                                <span>至</span>
                                <input type="number" :value="conditionBetweenBound(condition, 1)" placeholder="最大值" @input="setConditionBetweenBound(condition, 1, ($event.target as HTMLInputElement).value)" />
                              </div>
                              <input v-else type="number" :value="condition.value as any" placeholder="请输入数值" @input="setConditionValueFromEvent(condition, $event)" />
                            </div>

                            <div v-else-if="conditionFieldKind(condition.field) === 'selection'" class="condition-value-editor condition-checkbox-grid">
                              <label v-for="option in conditionSelectionOptions(condition)" :key="option">
                                <input type="checkbox" :checked="conditionSelectionIncludes(condition, option)" @change="toggleConditionSelectionValue(condition, option, ($event.target as HTMLInputElement).checked)" />
                                {{ option }}
                              </label>
                            </div>

                            <div v-else class="condition-value-editor condition-basic-editor">
                              <select v-model="condition.operator" @change="onBranchConditionOperatorChange(condition)">
                                <option v-for="operator in conditionOperatorOptions(condition)" :key="operator.value" :value="operator.value">{{ operator.label }}</option>
                              </select>
                              <select
                                v-if="conditionFieldKind(condition.field) === 'company'"
                                v-model="condition.value"
                                :disabled="conditionValueDisabled(condition)"
                                @change="markRulesDirty"
                              >
                                <option v-for="company in companyOptions" :key="company.id" :value="company.id">{{ company.short_name || company.name }}</option>
                              </select>
                              <select
                                v-else-if="conditionFieldKind(condition.field) === 'department'"
                                v-model="condition.value"
                                :disabled="conditionValueDisabled(condition)"
                                @change="markRulesDirty"
                              >
                                <option v-for="dept in departmentOptions" :key="dept.id" :value="dept.id">{{ dept.name }}</option>
                              </select>
                              <select
                                v-else-if="conditionFieldKind(condition.field) === 'member'"
                                v-model="condition.value"
                                :disabled="conditionValueDisabled(condition)"
                                @change="markRulesDirty"
                              >
                                <option v-for="member in memberOptions" :key="member.id" :value="member.id">{{ member.name }}</option>
                              </select>
                              <input
                                v-else
                                v-model="condition.value"
                                :disabled="conditionValueDisabled(condition)"
                                @input="markRulesDirty"
                              />
                            </div>
                            <button class="condition-delete-btn" type="button" aria-label="删除条件" @click="removeBranchCondition(conditionGroup, condition.uid)">删除</button>
                          </div>
                        </template>
                        <div class="branch-condition-actions">
                          <button class="secondary-btn" type="button" :disabled="availableConditionCountForGroup(conditionGroup) === 0" @click="openConditionPicker(conditionGroup)">+ 添加条件</button>
                          <span>还有{{ availableConditionCountForGroup(conditionGroup) }}个可用条件</span>
                        </div>
                      </section>
                    </template>
                    <button class="primary-outline-btn" type="button" @click="addConditionGroup(activeBranch)">+ 添加条件组</button>
                    <p class="branch-condition-help">ⓘ 如何添加更多条件</p>
                  </div>
                </div>
              </div>
              <div v-if="isParallelBranchNode(selectedFlowNode)" class="branch-editor branch-editor--simple">
                <p class="branch-default-note">并行分支会同时发起各分支中的节点，所有分支完成后自动汇合到后续流程。</p>
                <div v-if="activeBranch" class="branch-panel">
                  <label class="node-inline-field">
                    <span>分支名称</span>
                    <input v-model="activeBranch.label" placeholder="分支名称" @input="markRulesDirty" />
                  </label>
                  <p class="muted">请在画布中为该分支添加审批人、办理人、抄送人或自动节点。</p>
                </div>
              </div>
          </section>
        </div>

      </section>
    </main>

    <div v-if="addDialogVisible" class="modal-mask">
      <section class="add-modal" role="dialog" aria-modal="true" aria-label="添加模板">
        <button class="modal-close" type="button" aria-label="关闭" @click="addDialogVisible = false">×</button>
        <h2>添加模板</h2>
        <button class="custom-template" type="button" @click="startBlankTemplate">+ 自定义模板</button>
        <div class="modal-tabs"><b>从推荐模板添加</b><button type="button" class="link-btn" @click="startCopyTemplate">从已有模板复制</button></div>
        <div class="recommend-block" v-for="group in recommendedTemplateGroups" :key="group.name">
          <span>{{ group.name }}</span>
          <div class="recommend-grid">
            <button v-for="item in group.items" :key="item.name" type="button" @click="startRecommendedTemplate(item.name)">{{ item.name }}</button>
          </div>
        </div>
      </section>
    </div>

    <div v-if="flowDialogVisible" class="modal-mask">
      <section class="flow-dialog" role="dialog" aria-modal="true" aria-label="审批流程设置">
        <button class="modal-close" type="button" aria-label="关闭审批流程设置" @click="flowDialogVisible = false">×</button>
        <h2>审批流程设置</h2>
        <div class="flow-setting-layout">
          <div class="flow-lane">
            <div class="flow-card flow-start">申请人<br><small>提交后自动识别</small></div>
            <template v-for="(node, index) in flowNodes" :key="node.uid">
              <button class="flow-plus" type="button" @click="insertFlowNodeAt(index)">+</button>
              <article
                class="flow-card flow-node-card"
                :class="[nodeCardClass(node.node_type), { active: selectedFlowNodeUid === node.uid }]"
                role="button"
                tabindex="0"
                @click="openNodeSettings(node.uid)"
                @keydown.enter.prevent="openNodeSettings(node.uid)"
                @keydown.space.prevent="openNodeSettings(node.uid)"
              >
                <div>
                  {{ nodeTypeLabel(node.node_type) }}<br />
                  <small>{{ nodeAssigneeDisplay(node) }}</small>
                </div>
              </article>
            </template>
            <button class="flow-plus" type="button" @click="addFlowNode()">+</button>
            <div class="flow-end">流程结束</div>
          </div>

          <section class="flow-side-panel" v-if="selectedFlowNode">
            <header :class="{ 'flow-drawer-header--condition': isSplitFlowNode(selectedFlowNode) }">
              <template v-if="isSplitFlowNode(selectedFlowNode) && activeBranch">
                <h3 class="condition-drawer-title">
                  <input v-model="activeBranch.label" :aria-label="`${activeBranch.label || '分支'}名称`" :placeholder="isParallelBranchNode(selectedFlowNode) ? '分支' : '条件'" @input="markRulesDirty" />
                  <span aria-hidden="true">✎</span>
                </h3>
                <div class="flow-drawer-actions">
                  <select
                    v-if="isConditionBranchNode(selectedFlowNode) && !activeBranch.is_default_branch"
                    class="branch-priority-select"
                    :value="activeBranchPriority"
                    aria-label="条件优先级"
                    @change="setBranchPriority(activeBranch.uid, $event)"
                  >
                    <option v-for="(_, index) in selectedFlowNode.branches || []" :key="index" :value="index + 1">优先级{{ index + 1 }}</option>
                  </select>
                  <span class="condition-info-icon" aria-hidden="true">i</span>
                </div>
              </template>
              <template v-else>
                <h3>{{ nodeTypeLabel(selectedFlowNode.node_type) }}设置</h3>
                <button v-if="flowNodes.length > 1" class="link-btn" type="button" @click="removeFlowNode(selectedFlowNode.uid)">删除节点</button>
              </template>
            </header>

            <label v-if="!isSplitFlowNode(selectedFlowNode)">
              <span>节点类型</span>
              <select v-model="selectedFlowNode.node_type" @change="onFlowNodeTypeChange(selectedFlowNode)">
                <option value="approval">审批人</option>
                <option value="notify">抄送人</option>
                <option value="handler">办理人</option>
                <option value="condition_branch">条件分支</option>
                <option value="parallel_branch">并行分支</option>
                <option value="auto">自动处理</option>
              </select>
            </label>

            <template v-if="selectedFlowNode.node_type !== 'auto' && !isSplitFlowNode(selectedFlowNode)">
              <label>
                <span>指派方式</span>
                <select v-model="selectedFlowNode.assignee_source" @change="onAssigneeSourceChange(selectedFlowNode)">
                  <option value="direct_manager">直属上级</option>
                  <option value="department_head">部门负责人</option>
                  <option value="specific_user">指定成员</option>
                  <option value="role">按角色</option>
                </select>
              </label>

              <label v-if="selectedFlowNode.assignee_source === 'specific_user'">
                <span>选择成员</span>
                <div class="member-picker-inline">
                  <input :value="memberNamesForFlowNode(selectedFlowNode)" readonly :placeholder="selectedFlowNode.node_type === 'notify' ? '请选择，可多选' : '请选择'" />
                  <button class="secondary-btn" type="button" @click="openMemberPicker(selectedFlowNode)">选择</button>
                </div>
              </label>

              <label v-if="selectedFlowNode.assignee_source === 'role'">
                <span>选择角色</span>
                <select v-model="selectedFlowNode.role" @change="markRulesDirty">
                  <option value="manager">manager</option>
                  <option value="hr">hr</option>
                  <option value="admin">admin</option>
                  <option value="finance">finance</option>
                  <option value="asset_admin">asset_admin</option>
                </select>
              </label>
            </template>

            <label v-if="selectedFlowNode.node_type === 'approval'">
              <span>审批方式</span>
              <select v-model="selectedFlowNode.approval_mode" @change="markRulesDirty">
                <option value="or_sign">或签</option>
                <option value="counter_sign">会签</option>
              </select>
            </label>

            <fieldset v-if="selectedFlowNode.node_type === 'notify'" class="side-checks">
              <label><input v-model="selectedFlowNode.allow_applicant_select" type="checkbox" @change="markRulesDirty" /> 允许申请人自选抄送人</label>
              <label><input v-model="selectedFlowNode.include_applicant_self" type="checkbox" @change="markRulesDirty" /> 抄送给申请人本人</label>
            </fieldset>

            <div v-if="isConditionBranchNode(selectedFlowNode)" class="branch-editor branch-editor--simple">
              <p v-if="activeBranch?.is_default_branch" class="branch-default-note">默认条件会承接其他未命中的申请，无需额外设置。</p>

              <div v-else-if="activeBranch" class="branch-panel">
                <div class="branch-conditions branch-condition-builder branch-condition-builder--simple">
                  <template v-for="(conditionGroup, groupIndex) in activeBranch.condition_groups || []" :key="conditionGroup.uid">
                    <div v-if="groupIndex > 0" class="branch-group-separator">或</div>
                    <section class="branch-condition-group branch-condition-group--simple">
                      <template v-for="(condition, conditionIndex) in conditionGroup.conditions" :key="condition.uid">
                        <div v-if="conditionIndex > 0" class="branch-row-connector">且</div>
                        <div class="branch-condition-row branch-condition-row--builder branch-condition-row--simple">
                          <span class="branch-condition-label">{{ conditionFieldLabel(condition.field) }}</span>
                          <div v-if="conditionFieldKind(condition.field) === 'applicant'" class="condition-value-editor condition-scope-editor">
                            <button class="condition-scope-trigger" type="button" @click="openApplicantScopePicker(condition)">
                              <span v-for="item in conditionApplicantScopeValues(condition)" :key="applicantScopeKey(item)" class="condition-chip">{{ applicantScopeLabel(item) }}</span>
                              <span v-if="!conditionApplicantScopeValues(condition).length" class="muted">请选择具体人员/角色/部门</span>
                            </button>
                          </div>
                          <div v-else-if="conditionFieldKind(condition.field) === 'number'" class="condition-value-editor condition-number-editor">
                            <select v-model="condition.operator" @change="onBranchConditionOperatorChange(condition)">
                              <option v-for="operator in conditionOperatorOptions(condition)" :key="operator.value" :value="operator.value">{{ operator.label }}</option>
                            </select>
                            <div v-if="condition.operator === 'between'" class="condition-between-inputs">
                              <input type="number" :value="conditionBetweenBound(condition, 0)" placeholder="最小值" @input="setConditionBetweenBound(condition, 0, ($event.target as HTMLInputElement).value)" />
                              <span>至</span>
                              <input type="number" :value="conditionBetweenBound(condition, 1)" placeholder="最大值" @input="setConditionBetweenBound(condition, 1, ($event.target as HTMLInputElement).value)" />
                            </div>
                            <input v-else type="number" :value="condition.value as any" placeholder="请输入数值" @input="setConditionValueFromEvent(condition, $event)" />
                          </div>
                          <div v-else-if="conditionFieldKind(condition.field) === 'selection'" class="condition-value-editor condition-checkbox-grid">
                            <label v-for="option in conditionSelectionOptions(condition)" :key="option">
                              <input type="checkbox" :checked="conditionSelectionIncludes(condition, option)" @change="toggleConditionSelectionValue(condition, option, ($event.target as HTMLInputElement).checked)" />
                              {{ option }}
                            </label>
                          </div>
                          <div v-else class="condition-value-editor condition-basic-editor">
                            <select v-model="condition.operator" @change="onBranchConditionOperatorChange(condition)">
                              <option v-for="operator in conditionOperatorOptions(condition)" :key="operator.value" :value="operator.value">{{ operator.label }}</option>
                            </select>
                            <select v-if="conditionFieldKind(condition.field) === 'company'" v-model="condition.value" :disabled="conditionValueDisabled(condition)" @change="markRulesDirty">
                              <option v-for="company in companyOptions" :key="company.id" :value="company.id">{{ company.short_name || company.name }}</option>
                            </select>
                            <select v-else-if="conditionFieldKind(condition.field) === 'department'" v-model="condition.value" :disabled="conditionValueDisabled(condition)" @change="markRulesDirty">
                              <option v-for="dept in departmentOptions" :key="dept.id" :value="dept.id">{{ dept.name }}</option>
                            </select>
                            <select v-else-if="conditionFieldKind(condition.field) === 'member'" v-model="condition.value" :disabled="conditionValueDisabled(condition)" @change="markRulesDirty">
                              <option v-for="member in memberOptions" :key="member.id" :value="member.id">{{ member.name }}</option>
                            </select>
                            <input v-else v-model="condition.value" :disabled="conditionValueDisabled(condition)" @input="markRulesDirty" />
                          </div>
                          <button class="condition-delete-btn" type="button" aria-label="删除条件" @click="removeBranchCondition(conditionGroup, condition.uid)">删除</button>
                        </div>
                      </template>
                      <div class="branch-condition-actions">
                        <button class="secondary-btn" type="button" :disabled="availableConditionCountForGroup(conditionGroup) === 0" @click="openConditionPicker(conditionGroup)">+ 添加条件</button>
                        <span>还有{{ availableConditionCountForGroup(conditionGroup) }}个可用条件</span>
                      </div>
                    </section>
                  </template>
                  <button class="primary-outline-btn" type="button" @click="addConditionGroup(activeBranch)">+ 添加条件组</button>
                  <p class="branch-condition-help">ⓘ 如何添加更多条件</p>
                </div>
              </div>
            </div>
            <div v-if="isParallelBranchNode(selectedFlowNode)" class="branch-editor branch-editor--simple">
              <p class="branch-default-note">并行分支会同时发起各分支中的节点，所有分支完成后自动汇合到后续流程。</p>
              <div v-if="activeBranch" class="branch-panel">
                <label class="node-inline-field">
                  <span>分支名称</span>
                  <input v-model="activeBranch.label" placeholder="分支名称" @input="markRulesDirty" />
                </label>
                <p class="muted">请在画布中为该分支添加审批人、办理人、抄送人或自动节点。</p>
              </div>
            </div>
          </section>
        </div>
        <div class="flow-dialog-actions">
          <button class="secondary-btn" type="button" @click="addFlowNode()">+ 添加节点</button>
          <button class="secondary-btn" type="button" @click="addFlowNode('condition_branch')">+ 添加条件分支</button>
          <button class="secondary-btn" type="button" @click="addFlowNode('parallel_branch')">+ 添加并行分支</button>
          <button class="primary-btn simulate-btn" type="button" @click="flowDialogVisible = false">应用到规则</button>
        </div>
      </section>
    </div>

    <div v-if="memberPickerVisible" class="modal-mask">
      <section class="member-picker-modal" role="dialog" aria-modal="true" aria-label="组织结构选人">
        <button class="modal-close" type="button" aria-label="关闭组织结构选人" @click="closeMemberPicker">×</button>
        <h2>{{ memberPickerPurpose === 'multi_level_member' ? '选择主管链人员' : '选择成员' }}</h2>
        <p v-if="memberPickerMultiple" class="member-picker-hint">已选择 {{ tempSelectedMemberIds.length }} 人</p>
        <div v-if="memberPickerPurpose === 'multi_level_member'" class="member-tree-picker-layout">
          <label class="scope-search">
            <span>搜索人员</span>
            <input v-model="memberKeyword" placeholder="姓名/工号" />
          </label>
          <div class="scope-picker-layout">
            <section class="scope-tree-pane">
              <h3>组织架构</h3>
              <el-tree
                ref="memberTreeRef"
                class="scope-tree"
                :data="memberPickerTreeData"
                node-key="key"
                show-checkbox
                check-strictly
                :props="{ label: 'label', children: 'children', disabled: 'disabled' }"
                :default-expand-all="true"
                :filter-node-method="filterMemberTreeNode"
                @check="onMemberTreeCheck"
              />
            </section>
            <aside class="scope-selected-pane">
              <h3>已选择（{{ tempSelectedMemberIds.length }}）</h3>
              <div class="scope-selected-list">
                <span v-for="memberId in tempSelectedMemberIds" :key="memberId" class="condition-chip">
                  {{ memberCompactLabelById(memberId) || `员工#${memberId}` }}
                  <button type="button" aria-label="移除" @click="removeTempSelectedMember(memberId)">×</button>
                </span>
                <p v-if="!tempSelectedMemberIds.length" class="empty-tip">请选择主管链上的人员</p>
              </div>
            </aside>
          </div>
        </div>
        <div v-else class="member-picker-layout">
          <aside class="department-pane">
            <label>
              <span>搜索部门</span>
              <input v-model="departmentKeyword" placeholder="请输入部门名" />
            </label>
            <el-tree
              ref="departmentTreeRef"
              class="department-tree"
              :data="departmentTreeData"
              node-key="id"
              :props="{ label: 'name', children: 'children' }"
              :expand-on-click-node="false"
              :default-expand-all="true"
              :highlight-current="true"
              :filter-node-method="filterDepartmentNode"
              @node-click="onDepartmentTreeSelect"
            />
          </aside>
          <section class="employee-pane">
            <label>
              <span>搜索成员</span>
              <input v-model="memberKeyword" placeholder="姓名/工号" />
            </label>
            <div class="employee-list">
              <button
                v-for="member in filteredPickerMembers"
                :key="member.id"
                type="button"
                class="employee-item"
                :class="{ active: isTempMemberSelected(member.id), 'employee-item--multiple': memberPickerMultiple }"
                @click="toggleTempMember(member.id)"
              >
                <span v-if="memberPickerMultiple" class="employee-check" aria-hidden="true">{{ isTempMemberSelected(member.id) ? '✓' : '' }}</span>
                <span class="employee-info">
                  <strong>{{ member.name }}</strong>
                  <small>{{ member.department_name || departmentNameById(member.department_id) }} · {{ member.employee_no || '-' }}</small>
                </span>
              </button>
              <p v-if="!filteredPickerMembers.length" class="empty-tip">当前筛选条件下没有成员</p>
            </div>
          </section>
        </div>
        <div class="modal-actions">
          <button class="secondary-btn" type="button" @click="closeMemberPicker">取消</button>
          <button class="primary-btn" type="button" @click="confirmMemberPicker">确定</button>
        </div>
      </section>
    </div>

    <div v-if="rolePickerVisible" class="modal-mask role-picker-mask">
      <section class="role-picker-modal" role="dialog" aria-modal="true" aria-label="选择角色">
        <button class="modal-close" type="button" aria-label="关闭选择角色" @click="closeRolePicker">×</button>
        <h2>选择角色</h2>
        <div class="role-picker-layout">
          <el-tree
            ref="roleTreeRef"
            class="role-picker-tree"
            :data="roleTreeData"
            node-key="key"
            :props="{ label: 'label', children: 'children' }"
            :expand-on-click-node="false"
            :default-expand-all="true"
            :highlight-current="true"
            @node-click="onRoleTreeSelect"
          />
          <aside class="role-picker-selected">
            <strong>已选择</strong>
            <span v-if="tempSelectedRole" class="role-chip">{{ roleLabel(tempSelectedRole) }}</span>
            <p v-else class="muted">请从左侧树状图选择具体角色</p>
          </aside>
        </div>
        <div class="modal-actions">
          <button class="secondary-btn" type="button" @click="closeRolePicker">取消</button>
          <button class="primary-btn" type="button" :disabled="!tempSelectedRole" @click="confirmRolePicker">确定</button>
        </div>
      </section>
    </div>

    <div v-if="conditionPickerVisible" class="modal-mask">
      <section class="condition-picker-modal" role="dialog" aria-modal="true" aria-label="选择条件">
        <button class="modal-close" type="button" aria-label="关闭选择条件" @click="closeConditionPicker">×</button>
        <h2>选择条件</h2>
        <p>请选择用来区分审批流程的条件字段， 已选{{ tempConditionFieldValues.length }}个</p>
        <div class="condition-picker-options">
          <label v-for="field in conditionFieldOptions" :key="field.value">
            <input
              type="checkbox"
              :checked="tempConditionFieldValues.includes(field.value)"
              @change="toggleTempConditionField(field.value, ($event.target as HTMLInputElement).checked)"
            />
            {{ field.label }}
          </label>
        </div>
        <div class="modal-actions">
          <button class="secondary-btn" type="button" @click="closeConditionPicker">取消</button>
          <button class="primary-btn" type="button" @click="confirmConditionPicker">确定</button>
        </div>
      </section>
    </div>

    <div v-if="applicantScopePickerVisible" class="modal-mask">
      <section class="applicant-scope-modal" role="dialog" aria-modal="true" aria-label="选择发起人范围">
        <button class="modal-close" type="button" aria-label="关闭发起人范围选择" @click="closeApplicantScopePicker">×</button>
        <h2>选择发起人</h2>
        <label class="scope-search">
          <span>搜索</span>
          <input v-model="applicantScopeKeyword" placeholder="人员、角色、部门" />
        </label>
        <div class="scope-picker-layout">
          <section class="scope-tree-pane">
            <div class="scope-mode-tabs" role="tablist" aria-label="发起人选择方式">
              <button
                type="button"
                role="tab"
                :aria-selected="applicantScopeMode === 'org'"
                :class="{ active: applicantScopeMode === 'org' }"
                @click="setApplicantScopeMode('org')"
              >
                <component :is="Briefcase" />
                <span>按架构选</span>
              </button>
              <button
                type="button"
                role="tab"
                :aria-selected="applicantScopeMode === 'role'"
                :class="{ active: applicantScopeMode === 'role' }"
                @click="setApplicantScopeMode('role')"
              >
                <component :is="UserFilled" />
                <span>按角色选</span>
              </button>
            </div>

            <div v-if="applicantScopeMode === 'org'" class="scope-pane-body">
              <h3>人员/部门/公司</h3>
              <el-tree
                ref="applicantScopeTreeRef"
                class="scope-tree"
                :data="applicantOrgScopeTreeData"
                node-key="key"
                show-checkbox
                check-strictly
                :props="{ label: 'label', children: 'children', disabled: 'disabled' }"
                :default-expand-all="true"
                :filter-node-method="filterApplicantScopeNode"
                @check="onApplicantScopeTreeCheck"
              />
            </div>

            <div v-else class="scope-pane-body scope-role-pane">
              <h3>角色</h3>
              <div class="scope-role-list">
                <button
                  v-for="role in filteredApplicantRoleOptions"
                  :key="role.value"
                  type="button"
                  class="scope-role-item"
                  :class="{ active: isApplicantRoleSelected(role) }"
                  @click="toggleApplicantRoleScope(role)"
                >
                  <component :is="UserFilled" />
                  <span>
                    <strong>{{ role.label }}</strong>
                    <small>{{ role.value }}</small>
                  </span>
                </button>
                <p v-if="!filteredApplicantRoleOptions.length" class="empty-tip">当前筛选条件下没有角色</p>
              </div>
            </div>
          </section>
          <aside class="scope-selected-pane">
            <h3>已选择（{{ tempApplicantScopeValues.length }}）</h3>
            <div class="scope-selected-list">
              <span v-for="item in tempApplicantScopeValues" :key="applicantScopeKey(item)" class="condition-chip">
                {{ applicantScopeLabel(item) }}
                <button type="button" aria-label="移除" @click="removeTempApplicantScopeValue(item)">×</button>
              </span>
              <p v-if="!tempApplicantScopeValues.length" class="empty-tip">请选择公司、部门、成员或角色</p>
            </div>
          </aside>
        </div>
        <div class="modal-actions">
          <button class="secondary-btn" type="button" @click="closeApplicantScopePicker">取消</button>
          <button class="primary-btn" type="button" @click="confirmApplicantScopePicker">确定</button>
        </div>
      </section>
    </div>

    <div v-if="archiveApplicantPickerVisible" class="modal-mask">
      <section class="member-picker-modal" role="dialog" aria-modal="true" aria-label="选择申请人">
        <button class="modal-close" type="button" aria-label="关闭申请人选择" @click="closeArchiveApplicantPicker">×</button>
        <h2>选择申请人</h2>
        <p class="member-picker-hint">从组织架构中选择一个员工作为数据管理筛选条件。</p>
        <div class="member-picker-layout">
          <aside class="department-pane">
            <label>
              <span>搜索部门</span>
              <input v-model="archiveApplicantDepartmentKeyword" placeholder="请输入部门名" />
            </label>
            <el-tree
              ref="archiveDepartmentTreeRef"
              class="department-tree"
              :data="departmentTreeData"
              node-key="id"
              :props="{ label: 'name', children: 'children' }"
              :expand-on-click-node="false"
              :default-expand-all="true"
              :highlight-current="true"
              :filter-node-method="filterDepartmentNode"
              @node-click="onArchiveDepartmentTreeSelect"
            />
          </aside>
          <section class="employee-pane">
            <label>
              <span>搜索成员</span>
              <input v-model="archiveApplicantKeyword" placeholder="姓名/工号" />
            </label>
            <div class="employee-list">
              <button
                v-for="member in filteredArchiveApplicantMembers"
                :key="`archive-applicant-${member.id}`"
                type="button"
                class="employee-item"
                :class="{ active: archiveFilters.applicant_id === member.id }"
                @click="selectArchiveApplicant(member)"
              >
                <span class="employee-info">
                  <strong>{{ member.name }}</strong>
                  <small>{{ member.department_name || departmentNameById(member.department_id) }} · {{ member.employee_no || '-' }}</small>
                </span>
              </button>
              <p v-if="!filteredArchiveApplicantMembers.length" class="empty-tip">当前筛选条件下没有成员</p>
            </div>
          </section>
        </div>
        <div class="modal-actions">
          <button class="secondary-btn" type="button" @click="clearArchiveApplicantFilter">清除筛选</button>
          <button class="primary-btn" type="button" @click="closeArchiveApplicantPicker">完成</button>
        </div>
      </section>
    </div>

    <div v-if="approvalPreviewVisible" class="modal-mask approval-preview-mask" @click.self="closeApprovalPreview">
      <section class="approval-preview-dialog" role="dialog" aria-modal="true" aria-label="审批表单预览">
        <header class="approval-preview-header">
          <div>
            <h2>{{ currentTemplate.name }} · 预览</h2>
            <p>{{ approvalPreviewSubtitle }}</p>
          </div>
          <button class="modal-close" type="button" aria-label="关闭预览" @click="closeApprovalPreview">×</button>
        </header>

        <div class="approval-preview-layout">
          <aside class="approval-preview-sidebar" aria-label="预览摘要">
            <div class="approval-preview-device-tabs" role="group" aria-label="预览设备">
              <button type="button" :aria-pressed="approvalPreviewDevice === 'mobile'" @click="approvalPreviewDevice = 'mobile'">
                <component :is="IphoneIcon" aria-hidden="true" />
                <span>移动端</span>
              </button>
              <button type="button" :aria-pressed="approvalPreviewDevice === 'desktop'" @click="approvalPreviewDevice = 'desktop'">
                <component :is="DataBoard" aria-hidden="true" />
                <span>桌面端</span>
              </button>
            </div>
            <dl class="approval-preview-stats">
              <div><dt>发起范围</dt><dd>{{ submitPermissionLabel }}</dd></div>
              <div><dt>可见范围</dt><dd>{{ visibleScopeLabel }}</dd></div>
              <div><dt>表单字段</dt><dd>{{ editableFields.length }} 个，其中 {{ previewRequiredFieldCount }} 个必填</dd></div>
              <div><dt>流程状态</dt><dd>{{ flowPreviewText }}</dd></div>
            </dl>
          </aside>

          <section class="approval-preview-canvas" aria-label="表单填写预览">
            <article class="approval-preview-form" :class="`approval-preview-form--${approvalPreviewDevice}`">
              <header>
                <span class="template-icon" :class="currentTemplateColor">
                  <component :is="currentTemplateIcon" />
                </span>
                <div>
                  <strong>{{ currentTemplate.name }}</strong>
                  <small>{{ currentTemplate.description || '提交后按流程自动流转' }}</small>
                </div>
              </header>
              <div v-if="!approvalPreviewFields.length" class="approval-preview-empty">请先在表单设计中添加控件</div>
              <div v-else class="approval-preview-field-list">
                <div
                  v-for="field in approvalPreviewFields"
                  :key="field.code"
                  class="approval-preview-field"
                  :class="`approval-preview-field--${previewFieldKind(field)}`"
                >
                  <span>
                    {{ field.label }}
                    <i v-if="field.is_required">*</i>
                  </span>
                  <strong>{{ previewFieldValue(field) }}</strong>
                </div>
              </div>
            </article>
          </section>

          <aside class="approval-preview-flow" aria-label="审批流程预览">
            <h3>流程预览</h3>
            <div class="approval-preview-flow-list">
              <div class="approval-preview-flow-step approval-preview-flow-step--start">
                <span>起</span>
                <div><strong>申请人</strong><small>提交后自动识别</small></div>
              </div>
              <template v-for="entry in approvalPreviewFlowEntries" :key="entry.key">
                <div class="approval-preview-flow-line"></div>
                <div
                  class="approval-preview-flow-step"
                  :class="[`approval-preview-flow-step--${entry.kind}`, { 'is-matched': entry.matched }]"
                  :style="{ paddingLeft: `${entry.depth * 16}px` }"
                >
                  <span>{{ entry.icon }}</span>
                  <div>
                    <strong>{{ entry.title }}</strong>
                    <small>{{ entry.subtitle }}</small>
                  </div>
                </div>
              </template>
              <div class="approval-preview-flow-line"></div>
              <div class="approval-preview-flow-step approval-preview-flow-step--end">
                <span>完</span>
                <div><strong>流程结束</strong><small>归档到审批记录</small></div>
              </div>
            </div>
          </aside>
        </div>

        <footer class="approval-preview-footer">
          <button class="secondary-btn" type="button" @click="closeApprovalPreview">关闭</button>
          <button class="primary-btn" type="button" @click="saveTemplateConfig">保存当前配置</button>
        </footer>
      </section>
    </div>

    <div v-if="simulateDialogVisible" class="modal-mask">
      <section class="simulate-dialog simulate-dialog--interactive" role="dialog" aria-modal="true" aria-label="模拟提交">
        <button class="modal-close" type="button" aria-label="关闭模拟提交" @click="simulateDialogVisible = false">×</button>
        <h2>模拟提交</h2>
        <p class="simulate-subtitle">选择发起人并填写关键字段，实时检查这笔申请会命中哪条审批路径。</p>
        <div class="simulate-layout">
          <aside class="simulate-panel" aria-label="模拟申请信息">
            <label>
              <span>发起人</span>
              <select v-model="simulateApplicantId">
                <option :value="null">未指定</option>
                <option v-for="member in memberOptions" :key="member.id" :value="member.id">
                  {{ member.name }}{{ member.department_name ? ` · ${member.department_name}` : '' }}
                </option>
              </select>
            </label>
            <label>
              <span>发起人角色</span>
              <select v-model="simulateApplicantRole">
                <option v-for="role in simulationRoleOptions" :key="role.value" :value="role.value">{{ role.label }}</option>
              </select>
            </label>
            <label v-if="companyOptions.length">
              <span>所在公司</span>
              <select v-model="simulateApplicantCompanyId">
                <option :value="null">未指定</option>
                <option v-for="company in companyOptions" :key="company.id" :value="company.id">{{ company.short_name || company.name }}</option>
              </select>
            </label>
            <p class="simulate-context">
              {{ simulatedApplicant ? `${simulatedApplicant.name} · ${departmentNameById(simulatedApplicant.department_id)}` : '未选择发起人时，发起人条件不会命中具体成员或部门' }}
            </p>
          </aside>

          <section class="simulate-form" aria-label="模拟表单字段">
            <h3>模拟填写</h3>
            <div v-if="!approvalPreviewFields.length" class="approval-preview-empty">请先在表单设计中添加控件</div>
            <div v-else class="simulate-field-list">
              <label v-for="field in approvalPreviewFields" :key="`simulate-${field.code}`">
                <span>{{ field.label }}<i v-if="field.is_required">*</i></span>
                <select
                  v-if="simulationFieldControl(field) === 'select'"
                  v-model="simulationFormValues[field.code]"
                >
                  <option v-for="option in simulationFieldOptions(field)" :key="option" :value="option">{{ option }}</option>
                </select>
                <input
                  v-else-if="simulationFieldControl(field) === 'number'"
                  v-model="simulationFormValues[field.code]"
                  type="number"
                  placeholder="0"
                />
                <input
                  v-else-if="simulationFieldControl(field) === 'date'"
                  v-model="simulationFormValues[field.code]"
                  type="date"
                />
                <input
                  v-else
                  v-model="simulationFormValues[field.code]"
                  :placeholder="field.placeholder || '请输入'"
                />
              </label>
            </div>
          </section>

          <aside class="simulate-result" aria-label="模拟命中流程">
            <h3>命中流程</h3>
            <p>{{ simulationMatchedSummary }}</p>
            <div class="approval-preview-flow-list simulate-flow-list">
              <div class="approval-preview-flow-step approval-preview-flow-step--start">
                <span>起</span>
                <div><strong>申请人</strong><small>{{ simulatedApplicant?.name || '模拟申请人' }}</small></div>
              </div>
              <template v-for="entry in simulationFlowEntries" :key="entry.key">
                <div class="approval-preview-flow-line"></div>
                <div
                  class="approval-preview-flow-step"
                  :class="[`approval-preview-flow-step--${entry.kind}`, { 'is-matched': entry.matched }]"
                  :style="{ paddingLeft: `${entry.depth * 16}px` }"
                >
                  <span>{{ entry.icon }}</span>
                  <div>
                    <strong>{{ entry.title }}</strong>
                    <small>{{ entry.subtitle }}</small>
                  </div>
                </div>
              </template>
              <div class="approval-preview-flow-line"></div>
              <div class="approval-preview-flow-step approval-preview-flow-step--end">
                <span>完</span>
                <div><strong>流程结束</strong><small>归档到审批记录</small></div>
              </div>
            </div>
          </aside>
        </div>
      </section>
    </div>

    <div v-if="ruleDialogVisible" class="modal-mask">
      <section
        class="rule-modal"
        :class="{ 'rule-modal--member': activeRuleDialog === 'submit' || activeRuleDialog === 'templateAdmin' }"
        role="dialog"
        aria-modal="true"
        :aria-label="ruleDialogTitle"
      >
        <h2>{{ ruleDialogTitle }}</h2>
        <div v-if="activeRuleDialog === 'visible'" class="dialog-options">
          <label v-for="dept in departmentOptions" :key="`dept-${dept.id}`">
            <input v-model="ruleSettings.visibleDepartmentIds" type="checkbox" :value="dept.id" /> {{ dept.name }}
          </label>
          <label><input v-model="ruleSettings.includeSubDepartments" type="checkbox" aria-label="包含子部门" /> 包含子部门</label>
        </div>
        <div v-else-if="activeRuleDialog === 'submit' || activeRuleDialog === 'templateAdmin'" class="rule-member-picker">
          <p class="member-picker-hint">已选择 {{ selectedRuleMemberIds().length }} 人</p>
          <div class="member-picker-layout">
            <aside class="department-pane">
              <label>
                <span>搜索部门</span>
                <input v-model="ruleDepartmentKeyword" placeholder="请输入部门名" />
              </label>
              <el-tree
                ref="ruleDepartmentTreeRef"
                class="department-tree"
                :data="departmentTreeData"
                node-key="id"
                :props="{ label: 'name', children: 'children' }"
                :expand-on-click-node="false"
                :default-expand-all="true"
                :highlight-current="true"
                :filter-node-method="filterDepartmentNode"
                @node-click="onRuleDepartmentTreeSelect"
              />
            </aside>
            <section class="employee-pane">
              <label>
                <span>搜索成员</span>
                <input v-model="ruleMemberKeyword" placeholder="姓名/工号" />
              </label>
              <div class="employee-list">
                <button
                  v-for="member in filteredRuleMembers"
                  :key="`rule-member-${member.id}`"
                  type="button"
                  class="employee-item employee-item--multiple"
                  :class="{ active: isRuleMemberSelected(member.id) }"
                  @click="toggleRuleMember(member.id)"
                >
                  <span class="employee-check" aria-hidden="true">{{ isRuleMemberSelected(member.id) ? '✓' : '' }}</span>
                  <span class="employee-info">
                    <strong>{{ member.name }}</strong>
                    <small>{{ member.department_name || departmentNameById(member.department_id) }} · {{ member.employee_no || '-' }}</small>
                  </span>
                </button>
                <p v-if="!filteredRuleMembers.length" class="empty-tip">当前筛选条件下没有成员</p>
              </div>
            </section>
            <aside class="selected-member-pane">
              <strong>已选择（{{ selectedRuleMemberIds().length }}）</strong>
              <div class="selected-member-list">
                <span v-for="memberId in selectedRuleMemberIds()" :key="`selected-rule-member-${memberId}`">
                  {{ memberCompactLabelById(memberId) || memberNameById(memberId) }}
                  <button type="button" aria-label="移除成员" @click="toggleRuleMember(memberId)">×</button>
                </span>
                <p v-if="!selectedRuleMemberIds().length" class="empty-tip">暂未选择成员</p>
              </div>
            </aside>
          </div>
        </div>
        <div class="modal-actions">
          <button class="secondary-btn" type="button" @click="closeRuleDialog">取消</button>
          <button class="primary-btn" type="button" @click="confirmRuleDialog">确定</button>
        </div>
      </section>
    </div>

    <div v-if="dirtyRuleDialogVisible" class="modal-mask">
      <section class="rule-modal" role="dialog" aria-modal="true" aria-label="保存规则设置">
        <h2>保存规则设置</h2>
        <p>规则设置尚未保存，离开前是否保存？</p>
        <div class="modal-actions">
          <button class="secondary-btn" type="button" @click="stayOnRules">留在此页</button>
          <button class="primary-btn" type="button" @click="saveRulesAndContinue">保存并继续</button>
        </div>
      </section>
    </div>

    <div v-if="templateSortDialogVisible" class="modal-mask" @click.self="closeTemplateSortDialog">
      <section class="template-sort-modal" role="dialog" aria-modal="true" :aria-label="templateSortDialogTitle">
        <header class="template-sort-header">
          <div>
            <h2>{{ templateSortDialogTitle }}</h2>
            <p>{{ templateSortMode === 'groups' ? '调整分组展示顺序，保存后刷新仍保持该顺序。' : '调整当前分组内表单展示顺序，保存后刷新仍保持该顺序。' }}</p>
          </div>
          <button class="template-move-close" type="button" aria-label="关闭排序" @click="closeTemplateSortDialog">×</button>
        </header>
        <div class="template-sort-list">
          <div
            v-for="(item, index) in templateSortDraftItems"
            :key="item.key"
            class="template-sort-item"
            :class="{ disabled: item.disabled }"
          >
            <span class="template-sort-index">{{ index + 1 }}</span>
            <span class="template-sort-main">
              <strong>{{ item.label }}</strong>
              <small>{{ item.subtitle }}</small>
            </span>
            <span class="template-sort-actions">
              <button type="button" :disabled="index === 0" @click="moveTemplateSortItem(index, -1)">上移</button>
              <button type="button" :disabled="index === templateSortDraftItems.length - 1" @click="moveTemplateSortItem(index, 1)">下移</button>
            </span>
          </div>
          <p v-if="!templateSortDraftItems.length" class="empty-tip">暂无可排序项目</p>
        </div>
        <footer class="template-sort-footer">
          <button class="secondary-btn" type="button" :disabled="templateSortSaving" @click="closeTemplateSortDialog">取消</button>
          <button class="primary-btn" type="button" :disabled="templateSortSaving || !templateSortDraftItems.length" @click="confirmTemplateSort">
            {{ templateSortSaving ? '保存中...' : '保存排序' }}
          </button>
        </footer>
      </section>
    </div>

    <div v-if="templateMoveDialogVisible" class="modal-mask" @click.self="closeTemplateMoveDialog">
      <section class="template-move-modal" role="dialog" aria-modal="true" aria-label="移动审批表单分组">
        <header class="template-move-header">
          <h2>将「{{ movingTemplate?.name || '表单' }}」移动到...</h2>
          <button class="template-move-close" type="button" aria-label="关闭" @click="closeTemplateMoveDialog">×</button>
        </header>
        <div class="template-move-body">
          <div v-if="templateMoveNewGroupMode" class="template-move-new-row">
            <input
              v-model="templateMoveNewGroupName"
              maxlength="30"
              placeholder="请输入"
              @keydown.enter.prevent="confirmTemplateMoveNewGroup"
            />
            <span>{{ templateMoveNewGroupName.length }} / 30</span>
            <button type="button" aria-label="确认添加分组" @click="confirmTemplateMoveNewGroup">✓</button>
            <button type="button" aria-label="取消添加分组" @click="cancelTemplateMoveNewGroup">×</button>
          </div>
          <div class="template-move-group-list">
            <button
              v-for="groupName in templateMoveTargetGroups"
              :key="groupName"
              type="button"
              class="template-move-group"
              :class="{ selected: templateMoveTargetGroup === groupName, current: groupName === templateMoveCurrentGroup }"
              @click="selectTemplateMoveGroup(groupName)"
            >
              <span>{{ groupName }}</span>
              <em v-if="groupName === templateMoveCurrentGroup">当前所在组</em>
              <i v-else :class="{ checked: templateMoveTargetGroup === groupName }"></i>
            </button>
          </div>
        </div>
        <footer class="template-move-footer">
          <button class="template-move-add" type="button" @click="startTemplateMoveNewGroup">
            <component :is="Plus" aria-hidden="true" />
            <span>添加分组</span>
          </button>
          <div>
            <button class="secondary-btn" type="button" @click="closeTemplateMoveDialog">取消</button>
            <button class="primary-btn" type="button" @click="confirmTemplateMove">确定</button>
          </div>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  AlarmClock,
  ArrowDown,
  ArrowRight,
  Briefcase,
  Calendar,
  Avatar,
  Box,
  CircleCheck,
  CircleClose,
  CirclePlus,
  Coin,
  Collection,
  CollectionTag,
  CopyDocument,
  CreditCard,
  DataBoard,
  Delete,
  Document,
  DocumentChecked,
  DocumentDelete,
  Download,
  EditPen,
  Folder,
  Goods,
  Iphone as IphoneIcon,
  Key,
  LocationFilled,
  Memo,
  Money,
  OfficeBuilding,
  Operation,
  Opportunity,
  Plus,
  Position,
  Postcard,
  PriceTag,
  Reading,
  Refresh,
  Search,
  Service,
  Setting,
  ShoppingBag,
  ShoppingCart,
  Sort,
  Stamp,
  Suitcase,
  Switch,
  TakeawayBox,
  Tickets,
  Tools,
  Unlock,
  User,
  UserFilled,
  Van,
  Wallet,
  WalletFilled,
} from '@element-plus/icons-vue'
import { del, get, post, put } from '@/utils/request'
import {
  clearWebAuthSession,
  getWebAuthRoles,
  hasUsableWebAuthToken,
  isWebAuthSuperuser,
} from '@/utils/authSession'
import FlowDesignerCanvas from '@/components/approval/FlowDesignerCanvas.vue'

type Screen = 'overview' | 'settings'
type OverviewSection = 'forms' | 'data'
type SettingsTab = 'records' | 'template' | 'rules'
type EditorStep = 'basic' | 'form' | 'flow'
type FlowNodeType = 'approval' | 'notify' | 'handler' | 'auto' | 'condition_branch' | 'parallel_branch'
type AssigneeSource = 'direct_manager' | 'department_head' | 'multi_level_manager' | 'specific_user' | 'role' | 'applicant_self' | 'applicant_select' | 'related_member_field' | 'form_department_head'
type NodeConfigTab = 'assignee' | 'permissions'
type ApprovalExecutionType = 'manual' | 'auto_approve' | 'auto_reject'
type RuleMemberTarget = 'submit' | 'templateAdmin' | ''
type ConditionOperator = 'eq' | 'neq' | 'in' | 'not_in' | 'contains' | 'not_contains' | 'empty' | 'not_empty' | 'lt' | 'lte' | 'gt' | 'gte' | 'between'
type ConditionFieldKind = 'applicant' | 'company' | 'department' | 'member' | 'number' | 'selection' | 'text'
type ConditionFieldOption = {
  label: string
  value: string
  valueKind: ConditionFieldKind
  options?: string[]
}
type ApplicantScopeType = 'member' | 'department' | 'company' | 'role'
type ApplicantScopeValue = {
  type: ApplicantScopeType
  id?: number
  value?: string
  label?: string
}
type ApplicantScopeTreeNode = {
  key: string
  label: string
  disabled?: boolean
  scope?: ApplicantScopeValue
  children?: ApplicantScopeTreeNode[]
}
type ApplicantScopeMode = 'org' | 'role'
type ApplicantSelectScope = 'company' | 'selected_members' | 'role'
type RoleOption = { value: string; label: string }
type RolePickerField = 'role' | 'multi_level_role' | 'applicant_select_roles'
type MemberPickerPurpose = 'assignee' | 'applicant_select_scope' | 'multi_level_member'
type RoleTreeNode = {
  key: string
  label: string
  role?: RoleOption
  children?: RoleTreeNode[]
}
type FlowConditionValue = string | number | ApplicantScopeValue | Array<string | number | ApplicantScopeValue> | null
type FlowPreviewEntryKind = 'node' | 'condition' | 'branch'
type FlowPreviewEntry = {
  key: string
  kind: FlowPreviewEntryKind
  icon: string
  title: string
  subtitle: string
  depth: number
  matched?: boolean
}
type TemplateField = {
  label: string
  code: string
  field_type: string
  is_required: boolean
  placeholder?: string
  display_condition?: string
  printable?: boolean
  print_visible?: boolean
  options_json?: Record<string, any>
  is_business_calculation?: boolean
  is_readonly?: boolean
}
type ControlPane = 'controls' | 'groups' | 'relations'
type ControlDefinition = {
  key: string
  field_type: string
  label: string
  icon: string
  iconComponent?: Component
  default_code?: string
  badge?: string
  placeholder: string
  default_options?: string[]
  options_payload?: Record<string, any>
  required_by_default?: boolean
  supports_required: boolean
  supports_options: boolean
  supports_print: boolean
  readonly_by_default?: boolean
  business_calculation?: boolean
  attendance_component?: boolean
  component_type?: string
  component_fields?: TemplateField[]
  disabled?: boolean
  disabled_reason?: string
}
type ControlLibraryCategory = {
  key: string
  title: string
  controls: ControlDefinition[]
  note?: string
}
type TemplateMenuItem = {
  id?: number
  name: string
  scope: string
  color: string
  category?: string
  categoryKey?: string
  icon?: Component
  iconKey?: string
  iconTone?: string
  more?: boolean
  disabled?: boolean
  focused?: boolean
  note?: string
  description?: string
  business_code?: string
  is_active?: boolean
  status?: string
  version?: number
  created_at?: string
  updated_at?: string
  sort_order?: number
}
type TemplateMoreAction = 'copy' | 'move' | 'disable'
type TemplateSortMode = 'groups' | 'templates'
type TemplateSortDraftItem = {
  key: string
  label: string
  subtitle: string
  savedCount?: number
  disabled?: boolean
}
type FlowVersionMode = 'draft' | 'published'
type FlowVersionItem = {
  id: number
  approval_type_id: number
  version: number
  status: string
  published_at?: string
  snapshot_json?: Record<string, any>
}
type TemplateEditorSnapshot = {
  template: {
    id: number | null
    name: string
    group: string
    businessCode: string
    isActive: boolean
    status: string
    version: number
    createdAt: string
    description: string
  }
  fields: TemplateField[]
  ruleSettings: Record<string, any>
  flowNodes: FlowNodeEditor[]
  selectedFlowNodeUid: string
  rulesDirty: boolean
}
type TemplateGroup = {
  name: string
  items: TemplateMenuItem[]
}
type ApprovalTemplateCatalogItem = TemplateMenuItem & {
  business_code: string
  codeAliases?: string[]
  nameAliases?: string[]
}
type EmployeeOption = {
  id: number
  name: string
  employee_no?: string
  department_id?: number | null
  department_name?: string
  role_names?: string[]
}
type DepartmentOption = {
  id: number
  name: string
  parent_id?: number | null
}
type CompanyOption = {
  id: number
  name: string
  short_name?: string
  parent_company_id?: number | null
}
type FlowConditionEditor = {
  uid: string
  field: string
  operator: ConditionOperator
  value: FlowConditionValue
}
type FlowConditionGroupEditor = {
  uid: string
  label: string
  condition_combinator: 'and' | 'or'
  conditions: FlowConditionEditor[]
}
type FlowBranchEditor = {
  uid: string
  label: string
  is_default_branch: boolean
  condition_combinator: 'and' | 'or'
  conditions: FlowConditionEditor[]
  condition_group_combinator: 'and' | 'or'
  condition_groups: FlowConditionGroupEditor[]
  nodes: FlowNodeEditor[]
}
type FlowNodeEditor = {
  uid: string
  node_type: FlowNodeType
  assignee_source: AssigneeSource
  approver_id: number | null
  approver_ids: number[]
  role: string
  approval_mode: 'or_sign' | 'counter_sign' | 'sequential' | 'vote'
  allow_applicant_select: boolean
  include_applicant_self: boolean
  applicant_select_mode: 'single' | 'multiple'
  applicant_select_scope: ApplicantSelectScope
  applicant_select_member_ids: number[]
  applicant_select_roles: string[]
  notify_sources: AssigneeSource[]
  related_field_code: string
  related_member_field_code: string
  related_department_field_code: string
  manager_level: number
  fallback_to_upper_manager: boolean
  applicant_self_if_head: boolean
  multi_level_end_type: 'role' | 'level' | 'member'
  multi_level_role: string
  multi_level_member_ids: number[]
  multi_level_limit_enabled: boolean
  multi_level_limit_level: number
  multi_level_level: 'highest' | number
  empty_action: 'auto_approve' | 'auto_reject' | 'transfer_to_admin' | 'transfer_to_specific' | ''
  empty_member_id: number | null
  vote_pass_count: number
  auto_action: 'approve' | 'reject'
  branches?: FlowBranchEditor[]
  active_branch_uid?: string
}
type ApprovalArchiveRow = {
  id: number
  approval_type_name?: string
  business_type?: string
  module?: string
  applicant_id?: number
  applicant_name?: string
  current_node_order?: number
  current_node_name?: string
  current_node_status_label?: string
  status?: string
  created_at?: string
  updated_at?: string
  summary?: string
}

const router = useRouter()
const screen = ref<Screen>('overview')
const activeOverviewSection = ref<OverviewSection>('forms')
const settingsTab = ref<SettingsTab>('template')
const editorStep = ref<EditorStep>('basic')
const addDialogVisible = ref(false)
const templateSearchKeyword = ref('')
const activeTemplateCategory = ref('售后现场')
const expandedTemplateGroups = ref<Record<string, boolean>>({})
const templateBatchMode = ref(false)
const selectedTemplateIds = ref<number[]>([])
const activeTemplateMoreKey = ref('')
const templateMoveDialogVisible = ref(false)
const movingTemplate = ref<TemplateMenuItem | null>(null)
const templateMoveCurrentGroup = ref('')
const templateMoveTargetGroup = ref('')
const templateMoveNewGroupMode = ref(false)
const templateMoveNewGroupName = ref('')
const templateSortDialogVisible = ref(false)
const templateSortMode = ref<TemplateSortMode>('groups')
const templateSortGroupName = ref('')
const templateSortDraftItems = ref<TemplateSortDraftItem[]>([])
const templateSortSaving = ref(false)
const formManagerStickyRef = ref<HTMLElement | null>(null)
const formManagerStickyHeight = ref(170)
let formManagerResizeObserver: ResizeObserver | null = null
const activeControlPane = ref<ControlPane>('controls')
const expandedControlCategories = ref<Record<string, boolean>>({
  layout: true,
  basic: true,
  enhanced: true,
  business: true,
  advanced: true,
  attendance: true,
  relation: true,
})
const formPreviewDevice = ref<'desktop' | 'mobile'>('mobile')
const approvalPreviewVisible = ref(false)
const approvalPreviewDevice = ref<'desktop' | 'mobile'>('mobile')
const flowDialogVisible = ref(false)
const simulateDialogVisible = ref(false)
const simulateApplicantId = ref<number | null>(null)
const simulateApplicantRole = ref('employee')
const simulateApplicantCompanyId = ref<number | null>(null)
const simulationFormValues = reactive<Record<string, any>>({})
const ruleDialogVisible = ref(false)
const dirtyRuleDialogVisible = ref(false)
const activeRuleDialog = ref<'visible' | 'submit' | 'templateAdmin' | ''>('')
const pendingSettingsTab = ref<SettingsTab | null>(null)
const pendingEditorStep = ref<EditorStep | null>(null)
const rulesDirty = ref(false)
const flowVersionMenuVisible = ref(false)
const currentFlowVersionMode = ref<FlowVersionMode>('published')
const flowVersions = ref<FlowVersionItem[]>([])
const selectedFlowVersionId = ref<number | null>(null)
const draftTemplateSnapshot = ref<TemplateEditorSnapshot | null>(null)
const selectedFlowNodeUid = ref('')
const flowConfigDrawerVisible = ref(false)
const flowZoom = ref(100)
const memberPickerVisible = ref(false)
const memberPickerPurpose = ref<MemberPickerPurpose>('assignee')
const rolePickerVisible = ref(false)
const applicantScopePickerVisible = ref(false)
const conditionPickerVisible = ref(false)
const archiveApplicantPickerVisible = ref(false)
const departmentKeyword = ref('')
const memberKeyword = ref('')
const pickerDepartmentId = ref<number | null>(null)
const applicantScopeKeyword = ref('')
const applicantScopeMode = ref<ApplicantScopeMode>('org')
const archiveApplicantDepartmentKeyword = ref('')
const archiveApplicantKeyword = ref('')
const archiveApplicantDepartmentId = ref<number | null>(null)
const ruleDepartmentKeyword = ref('')
const ruleMemberKeyword = ref('')
const rulePickerDepartmentId = ref<number | null>(null)
const tempSelectedMemberId = ref<number | null>(null)
const tempSelectedMemberIds = ref<number[]>([])
const memberPickerTargetNode = ref<FlowNodeEditor | null>(null)
const rolePickerTargetNode = ref<FlowNodeEditor | null>(null)
const rolePickerTargetField = ref<RolePickerField>('role')
const tempSelectedRole = ref('')
const applicantScopeTargetCondition = ref<FlowConditionEditor | null>(null)
const tempApplicantScopeValues = ref<ApplicantScopeValue[]>([])
const conditionPickerTargetGroup = ref<FlowConditionGroupEditor | null>(null)
const tempConditionFieldValues = ref<string[]>([])
const nodeConfigTab = ref<NodeConfigTab>('assignee')
const flowNodes = ref<FlowNodeEditor[]>([])
const departmentTreeRef = ref<any>(null)
const memberTreeRef = ref<any>(null)
const roleTreeRef = ref<any>(null)
const applicantScopeTreeRef = ref<any>(null)
const archiveDepartmentTreeRef = ref<any>(null)
const ruleDepartmentTreeRef = ref<any>(null)
const memberOptions = ref<EmployeeOption[]>([])
const departmentOptions = ref<DepartmentOption[]>([])
const companyOptions = ref<CompanyOption[]>([])
const currentTemplate = reactive({
  id: null as number | null,
  name: '新审批表单',
  group: '售后现场',
  businessCode: '',
  description: '',
  isActive: true,
  status: 'enabled',
  version: 1,
  createdAt: '',
})
const archiveFilters = reactive({
  start_date: '',
  end_date: '',
  business_type: '',
  status: '',
  applicant_id: null as number | null,
  applicant_keyword: '',
})
const approvalArchiveRows = ref<ApprovalArchiveRow[]>([])
const approvalArchiveTotal = ref(0)
const approvalArchiveLoading = ref(false)
const archiveContextBusinessType = ref<string | null>(null)
const templateStatusUpdatingId = ref<number | null>(null)
const editableFields = ref<TemplateField[]>([])
const selectedFieldCode = ref('')
const selectedDetailChildCode = ref('')
const detailChildTargetCode = ref('')
const draggedFieldCode = ref('')
const dragOverFieldCode = ref('')
const draggedControlKey = ref('')
const ruleSettings = reactive({
  visibleDepartmentIds: [] as number[],
  includeSubDepartments: false,
  submitPermissionType: 'all',
  submitRoles: [] as string[],
  submitMemberIds: [] as number[],
  viewPermissionType: 'all',
  templateAdminType: 'all',
  templateAdminIds: [] as number[],
  fixedApproverLocked: true,
  fixedCcLocked: true,
  fixedHandlerLocked: true,
  approvalCommentRequired: false,
  handlerCommentRequired: false,
})
const TEMPLATE_CONFIG_ROLES = new Set(['admin', 'hr'])
const approvalRoleOptions: RoleOption[] = [
  { value: 'manager', label: '经理' },
  { value: 'hr', label: 'HR' },
  { value: 'admin', label: '管理员' },
  { value: 'finance', label: '财务' },
  { value: 'asset_admin', label: '资产管理员' },
]
const approvalRoleTreeGroups = [
  { key: 'management', label: '管理角色', roles: ['manager', 'hr', 'admin'] },
  { key: 'functional', label: '职能角色', roles: ['finance', 'asset_admin'] },
] as const
const managerLevelOptions = [
  { value: 1, label: '直接主管' },
  { value: 2, label: '第2级主管' },
  { value: 3, label: '第3级主管' },
  { value: 4, label: '第4级主管' },
  { value: 5, label: '第5级主管' },
  { value: 6, label: '第6级主管' },
  { value: 7, label: '第7级主管' },
  { value: 8, label: '第8级主管' },
]
const applicantSelectModeOptions = [
  { value: 'multiple', label: '自选多个人' },
  { value: 'single', label: '自选一个人' },
] as const
const applicantSelectScopeOptions = [
  { value: 'company', label: '全公司' },
  { value: 'selected_members', label: '指定成员' },
  { value: 'role', label: '角色' },
] as const
const multiLevelDirectoryOptions = [
  { value: 'highest', label: '最高层级主管' },
  { value: 2, label: '第2个层级主管' },
  { value: 3, label: '第3个层级主管' },
  { value: 4, label: '第4个层级主管' },
  { value: 5, label: '第5个层级主管' },
  { value: 6, label: '第6个层级主管' },
  { value: 7, label: '第7个层级主管' },
  { value: 8, label: '第8个层级主管' },
] as const
const approvalEmptyActionOptions = [
  { value: 'auto_approve', label: '自动通过' },
  { value: 'auto_reject', label: '自动拒绝' },
  { value: 'transfer_to_admin', label: '自动转交管理员' },
  { value: 'transfer_to_specific', label: '指定人员审批' },
]
const unsupportedNotifyOptions = [
  { label: '负责人', reason: '负责人角色未绑定可解析的数据源，暂不可选' },
  { label: '从连接器获取', reason: '连接器抄送源尚未接入审批运行规则，暂不可选', trial: true },
]
const templateCategoryDefinitions = [
  {key:'field',name:'售后现场',aliases:['field','售后现场']},
  {key:'attendance',name:'假勤管理',aliases:['假勤','考勤','attendance']},
  {key:'other',name:'其他',aliases:['other','general','default']},
  {key:'disabled',name:'已停用',aliases:['disabled','inactive']},
]
const templateCategoryOrder = new Map(templateCategoryDefinitions.map((item, index) => [item.name, index]))
const templateCategoryNameSet = new Set(templateCategoryDefinitions.map((item) => item.name))
const defaultLeaveTypeConditionOptions = ['年假', '婚假', '产休假', '陪产假', '病假', '调休', '事假', '病假（长期）', '产检假', '丧假', '病假（通用）']
const conditionFieldAliases: Record<string, string> = {
  leave_duration: 'duration',
  outside_duration: 'duration',
  trip_duration: 'duration',
  overtime_duration: 'duration',
}

function getStoredRoles() {
  return getWebAuthRoles()
}

const canManageTemplateConfigs = computed(() => {
  if (isWebAuthSuperuser()) {
    return true
  }
  return getStoredRoles().some((role) => TEMPLATE_CONFIG_ROLES.has(role))
})
const approvalFormManagerStyle = computed(() => ({
  '--form-manager-sticky-height': `${formManagerStickyHeight.value}px`,
}))

function updateFormManagerStickyHeight() {
  const height = formManagerStickyRef.value?.offsetHeight
  if (height) {
    formManagerStickyHeight.value = Math.ceil(height)
  }
}

function templateCategoryKeyForName(name: string) {
  return templateCategoryDefinitions.find((category) => category.name === name)?.key || 'other'
}

function templateCategoryKeyForPayload(name: string, catalogItem?: ApprovalTemplateCatalogItem | null) {
  if (!templateCategoryNameSet.has(name)) {
    return null
  }
  if (catalogItem?.category === name && catalogItem.categoryKey) {
    return catalogItem.categoryKey
  }
  return templateCategoryKeyForName(name)
}

function catalogTemplate(options: {
  name: string
  business_code: string
  category: string
  icon: Component
  iconKey: string
  color: string
  codeAliases?: string[]
  nameAliases?: string[]
  note?: string
  scope?: string
  focused?: boolean
}): ApprovalTemplateCatalogItem {
  return {
    ...options,
    categoryKey: templateCategoryKeyForName(options.category),
    iconTone: options.color,
    icon: markRaw(options.icon),
    scope: options.scope || '全部可见',
    more: true,
  }
}

const approvalTemplateCatalog: ApprovalTemplateCatalogItem[] = []
const approvalArchiveTypeOptions = computed(() => templateGroups.value.flatMap(group=>group.items).map((item) => ({
  label: item.name,
  value: item.business_code,
})))
const videoHrBusinessCodes = new Set([
  'legal_overtime',
  'overtime',
  'overtime_holiday',
  'holiday_overtime',
  'leave',
  'leave_request',
  'business_trip',
  'travel',
  'outside',
  'out',
  'fieldwork',
  'resignation',
  'recruitment',
  'recruitment_demand',
  'punch_correction',
  'attendance_punch_correction',
])
const videoHrTemplateNames = new Set(['法定节假日加班申请', '加班', '请假', '出差', '外出', '离职', '离职申请', '招聘需求', '打卡补卡', '补卡'])
const legacyHrBusinessCodes = new Set(['headcount_request', 'headcount_plan', 'probation_application', 'probation', 'regularization', 'job_transfer', 'employee_transfer', 'transfer', 'sub_admin_permission', 'sub_admin_access'])
const legacyHrTemplateNames = new Set(['用人审批', '转正申请', '调岗申请', '子管理员权限申请'])
const videoHrTemplateOrder = new Map(
  approvalTemplateCatalog
    .filter((item) => item.category === '人事')
    .map((item, index) => [item.name, index]),
)

const recommendedTemplateGroups: TemplateGroup[] = [
  ...templateCategoryDefinitions
    .filter((category) => category.name !== '已停用')
    .map((category) => ({
      name: category.name,
      items: approvalTemplateCatalog
        .filter((item) => item.category === category.name)
        .map((item) => ({ ...item })),
    }))
    .filter((group) => group.items.length),
]
const templateGroups = ref<TemplateGroup[]>([])

const templateGroupOptions = computed(() => {
  const dynamicGroups = templateGroups.value.map((group) => group.name)
  return Array.from(new Set([
    ...templateCategoryDefinitions.filter((category) => category.name !== '已停用').map((category) => category.name),
    ...dynamicGroups.filter((name) => name !== '已停用'),
  ]))
})
const templateMoveTargetGroups = computed(() => {
  const currentGroup = templateMoveCurrentGroup.value
  return Array.from(new Set([
    ...templateGroupOptions.value.filter((name) => name !== '已停用'),
    currentGroup && currentGroup !== '已停用' ? currentGroup : '',
  ].filter(Boolean)))
})
const templateCategoryTabs = computed(() => {
  const groupCounts = new Map(templateGroups.value.map((group) => [group.name, group.items.length]))
  const customGroups = templateGroups.value
    .filter((group) => !templateCategoryNameSet.has(group.name))
    .map((group) => ({ name: group.name, count: group.items.length }))
  return [
    ...templateCategoryDefinitions.map((category) => ({ name: category.name, count: groupCounts.get(category.name) || 0 })),
    ...customGroups,
  ]
})
const visibleTemplateGroups = computed<TemplateGroup[]>(() => {
  const keyword = templateSearchKeyword.value.trim().toLowerCase()
  return templateGroups.value
    .filter((group) => keyword || group.name === activeTemplateCategory.value)
    .map((group) => ({
      name: group.name,
      items: keyword
        ? group.items.filter((item) => [
          item.name,
          item.business_code,
          item.note,
          item.scope,
          item.category,
          item.categoryKey,
          item.iconKey,
        ].some((value) => String(value || '').toLowerCase().includes(keyword)))
        : group.items,
    }))
    .filter((group) => group.items.length || (!keyword && group.name === activeTemplateCategory.value))
})
const commonControlDefinitions: ControlDefinition[] = [
  { key: 'layout_column', field_type: 'layout_column', label: '分栏', icon: 'Ⅱ', iconComponent: markRaw(DataBoard), placeholder: '分栏布局', supports_required: false, supports_options: false, supports_print: false, readonly_by_default: true },
  { key: 'divider', field_type: 'static_text', label: '分隔符', icon: '—', iconComponent: markRaw(Operation), default_code: 'section_divider', placeholder: '分隔线', supports_required: false, supports_options: false, supports_print: false, readonly_by_default: true, options_payload: { style: 'divider' } },
  { key: 'text', field_type: 'text', label: '单行输入框', icon: 'Aa', iconComponent: markRaw(EditPen), placeholder: '请输入', supports_required: true, supports_options: false, supports_print: true },
  { key: 'textarea', field_type: 'textarea', label: '多行输入框', icon: 'A≡', iconComponent: markRaw(Memo), placeholder: '请输入', supports_required: true, supports_options: false, supports_print: true },
  { key: 'number', field_type: 'number', label: '数字输入框', icon: '123', iconComponent: markRaw(DataBoard), placeholder: '请输入数字', supports_required: true, supports_options: false, supports_print: true },
  { key: 'radio', field_type: 'radio', label: '单选框', icon: '○', iconComponent: markRaw(CircleCheck), placeholder: '请选择', default_options: ['选项1', '选项2'], supports_required: true, supports_options: true, supports_print: true },
  { key: 'checkbox', field_type: 'checkbox', label: '多选框', icon: '☑', iconComponent: markRaw(CircleCheck), placeholder: '请选择', default_options: ['选项1', '选项2'], supports_required: true, supports_options: true, supports_print: true },
  { key: 'date', field_type: 'date', label: '日期', icon: '日', iconComponent: markRaw(Calendar), placeholder: '请选择', supports_required: true, supports_options: false, supports_print: true },
  { key: 'date_range', field_type: 'date_range', label: '日期区间', icon: '间', iconComponent: markRaw(Calendar), placeholder: '请选择日期区间', supports_required: true, supports_options: false, supports_print: true },
  { key: 'duration', field_type: 'duration', label: '时长', icon: '时', iconComponent: markRaw(AlarmClock), default_code: 'duration', placeholder: '0天', supports_required: true, supports_options: false, supports_print: true, readonly_by_default: true, business_calculation: true, options_payload: { time_scale: 'day', unit_hours: 24, duration_mode: 'natural_day' } },
  { key: 'static_text', field_type: 'static_text', label: '说明文字', icon: '文', iconComponent: markRaw(Operation), placeholder: '请输入说明文字', supports_required: false, supports_options: false, supports_print: false, readonly_by_default: true },
  { key: 'identity_card', field_type: 'identity_card', label: '身份证', icon: '证', iconComponent: markRaw(Postcard), placeholder: '请输入身份证号', supports_required: true, supports_options: false, supports_print: true },
  { key: 'phone', field_type: 'phone', label: '电话', icon: '机', iconComponent: markRaw(IphoneIcon), placeholder: '请输入手机号', supports_required: true, supports_options: false, supports_print: true },
  { key: 'datetime', field_type: 'datetime', label: '日期时间', icon: '时', iconComponent: markRaw(AlarmClock), placeholder: '请选择时间', supports_required: true, supports_options: false, supports_print: true },
  { key: 'select', field_type: 'select', label: '下拉选择', icon: '选', iconComponent: markRaw(ArrowDown), placeholder: '请选择', default_options: ['选项1', '选项2'], supports_required: true, supports_options: true, supports_print: true },
  { key: 'cascade', field_type: 'cascade', label: '级联/分类', icon: '级', iconComponent: markRaw(Collection), placeholder: '请选择', default_options: ['分类一', '分类二'], supports_required: true, supports_options: true, supports_print: true },
  { key: 'ai_control', field_type: 'ai_control', label: 'AI控件', icon: 'AI', iconComponent: markRaw(Operation), placeholder: '智能识别', supports_required: false, supports_options: false, supports_print: true, badge: '限时免费', disabled: true, disabled_reason: 'AI控件需接入模型服务后启用' },
  { key: 'image', field_type: 'image', label: '图片', icon: '图', iconComponent: markRaw(Collection), placeholder: '上传图片', supports_required: false, supports_options: false, supports_print: true },
  { key: 'detail', field_type: 'detail', label: '明细/表格', icon: '表', iconComponent: markRaw(Tickets), placeholder: '添加明细', supports_required: false, supports_options: false, supports_print: true, business_calculation: true },
  {
    key: 'trip_expense_detail',
    field_type: 'detail',
    label: '出差费用明细',
    icon: '差',
    iconComponent: markRaw(Suitcase),
    default_code: 'trip_expense_detail',
    placeholder: '添加明细',
    supports_required: true,
    supports_options: false,
    supports_print: true,
    required_by_default: true,
    business_calculation: true,
    options_payload: {
      child_fields: [
        { label: '机票', code: 'flight_ticket', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
        { label: '火车票', code: 'train_ticket', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
        { label: '住宿', code: 'lodging', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
        { label: '出差补助', code: 'trip_allowance', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
        { label: '市内交通', code: 'local_transport', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
      ],
      summary: {
        enabled: true,
        label: '预计出差费用合计',
        field_codes: ['flight_ticket', 'train_ticket', 'lodging', 'trip_allowance', 'local_transport'],
        value_type: 'amount',
      },
      print_layout: 'multi_line',
    },
  },
  {
    key: 'trip_itinerary_detail',
    field_type: 'detail',
    label: '出差行程明细',
    icon: '行',
    iconComponent: markRaw(Position),
    default_code: 'trip_itinerary_detail',
    placeholder: '添加行程',
    supports_required: true,
    supports_options: false,
    supports_print: true,
    required_by_default: true,
    business_calculation: true,
    options_payload: {
      child_fields: [
        { label: '开始时间', code: 'itinerary_start_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true },
        { label: '结束时间', code: 'itinerary_end_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true },
        { label: '出发地', code: 'departure_city', field_type: 'location', is_required: true, placeholder: '请输入', printable: true },
        { label: '目的地', code: 'destination_city', field_type: 'location', is_required: true, placeholder: '请输入', printable: true },
        { label: '交通方式', code: 'transport_method', field_type: 'select', is_required: true, placeholder: '请选择', printable: true, options_json: { options: ['飞机', '火车', '汽车', '自驾', '其他'] } },
        { label: '单程/往返', code: 'trip_way', field_type: 'radio', is_required: false, placeholder: '请选择', printable: true, options_json: { options: ['单程', '往返'] } },
        { label: '备注', code: 'itinerary_remark', field_type: 'textarea', is_required: false, placeholder: '请输入', printable: true },
      ],
      print_layout: 'multi_line',
    },
  },
  {
    key: 'expense_detail',
    field_type: 'detail',
    label: '报销明细',
    icon: '报',
    iconComponent: markRaw(PriceTag),
    default_code: 'expense_detail',
    placeholder: '添加明细',
    supports_required: true,
    supports_options: false,
    supports_print: true,
    required_by_default: true,
    business_calculation: true,
    options_payload: {
      child_fields: [
        { label: '费用类型', code: 'expense_type', field_type: 'select', is_required: true, placeholder: '请选择', printable: true, options_json: { options: ['交通费', '住宿费', '餐饮费', '办公费', '招待费', '其他'] } },
        { label: '发生日期', code: 'expense_date', field_type: 'date', is_required: true, placeholder: '请选择', printable: true },
        { label: '金额', code: 'expense_amount', field_type: 'amount', is_required: true, placeholder: '请输入金额', printable: true },
        { label: '费用说明', code: 'expense_remark', field_type: 'textarea', is_required: false, placeholder: '请输入', printable: true },
      ],
      summary: {
        enabled: true,
        label: '报销金额合计',
        field_codes: ['expense_amount'],
        value_type: 'amount',
      },
      print_layout: 'multi_line',
    },
  },
  {
    key: 'payment_collection_detail',
    field_type: 'detail',
    label: '收付款明细',
    icon: '款',
    iconComponent: markRaw(Wallet),
    default_code: 'payment_collection_detail',
    placeholder: '添加明细',
    supports_required: true,
    supports_options: false,
    supports_print: true,
    required_by_default: true,
    business_calculation: true,
    options_payload: {
      child_fields: [
        { label: '款项类型', code: 'payment_type', field_type: 'select', is_required: true, placeholder: '请选择', printable: true, options_json: { options: ['付款', '收款', '转账', '退款'] } },
        { label: '往来对象', code: 'counterparty', field_type: 'text', is_required: true, placeholder: '请输入', printable: true },
        { label: '金额', code: 'payment_amount', field_type: 'amount', is_required: true, placeholder: '请输入金额', printable: true },
        { label: '说明', code: 'payment_remark', field_type: 'textarea', is_required: false, placeholder: '请输入', printable: true },
      ],
      summary: {
        enabled: true,
        label: '收付款金额合计',
        field_codes: ['payment_amount'],
        value_type: 'amount',
      },
      print_layout: 'multi_line',
    },
  },
  { key: 'amount', field_type: 'amount', label: '金额', icon: '¥', iconComponent: markRaw(Money), placeholder: '请输入金额', supports_required: true, supports_options: false, supports_print: true },
  { key: 'attachment', field_type: 'attachment', label: '附件', icon: '附', iconComponent: markRaw(Download), placeholder: '上传附件', supports_required: false, supports_options: false, supports_print: true },
  { key: 'signature', field_type: 'signature', label: '手写签名', icon: '签', iconComponent: markRaw(EditPen), placeholder: '请签名', supports_required: false, supports_options: false, supports_print: true },
  { key: 'external_contact', field_type: 'external_contact', label: '外部联系人', icon: '外', iconComponent: markRaw(Service), placeholder: '请选择外部联系人', supports_required: true, supports_options: false, supports_print: true },
  { key: 'member', field_type: 'member', label: '联系人', icon: '人', iconComponent: markRaw(User), placeholder: '请选择成员', supports_required: true, supports_options: false, supports_print: true },
  { key: 'department', field_type: 'department', label: '部门', icon: '部', iconComponent: markRaw(OfficeBuilding), placeholder: '请选择部门', supports_required: true, supports_options: false, supports_print: true },
  { key: 'company', field_type: 'company', label: '公司', icon: '司', iconComponent: markRaw(OfficeBuilding), placeholder: '请选择公司', supports_required: true, supports_options: false, supports_print: true },
  { key: 'industry_department', field_type: 'industry_department', label: '行业通讯录部门', icon: '讯', iconComponent: markRaw(OfficeBuilding), placeholder: '请选择部门', supports_required: true, supports_options: false, supports_print: true },
  { key: 'location', field_type: 'location', label: '地点', icon: '址', iconComponent: markRaw(LocationFilled), placeholder: '请选择地点', supports_required: true, supports_options: false, supports_print: true },
  { key: 'formula', field_type: 'formula', label: '计算公式', icon: 'fx', iconComponent: markRaw(Operation), placeholder: '自动计算', supports_required: false, supports_options: false, supports_print: true, readonly_by_default: true, business_calculation: true },
  { key: 'related_approval', field_type: 'related_approval', label: '关联审批单', icon: '关', iconComponent: markRaw(Switch), placeholder: '请选择申请单', supports_required: false, supports_options: false, supports_print: true },
  { key: 'province_city', field_type: 'province_city', label: '省市区', icon: '省', iconComponent: markRaw(LocationFilled), placeholder: '请选择省市区', supports_required: true, supports_options: false, supports_print: true },
  { key: 'rating', field_type: 'rating', label: '评分', icon: '☆', iconComponent: markRaw(CircleCheck), placeholder: '请选择评分', supports_required: true, supports_options: false, supports_print: true },
  { key: 'invoice', field_type: 'invoice', label: '发票', icon: '票', iconComponent: markRaw(Tickets), placeholder: '请选择发票', supports_required: true, supports_options: false, supports_print: true },
  { key: 'customer', field_type: 'customer', label: '客户', icon: '客', iconComponent: markRaw(UserFilled), placeholder: '请选择客户', supports_required: true, supports_options: false, supports_print: true },
  { key: 'collection_account', field_type: 'collection_account', label: '收款账户', icon: '卡', iconComponent: markRaw(CreditCard), placeholder: '请选择收款账户', supports_required: true, supports_options: false, supports_print: true },
  { key: 'budget_request', field_type: 'budget_request', label: '预算申请', icon: '预', iconComponent: markRaw(Box), placeholder: '请选择预算申请', supports_required: true, supports_options: false, supports_print: true },
  { key: 'related_contract', field_type: 'related_contract', label: '关联合同', icon: '合', iconComponent: markRaw(DocumentChecked), placeholder: '请选择合同', supports_required: false, supports_options: false, supports_print: true },
  { key: 'engineering_project', field_type: 'engineering_project', label: '工程项目', icon: '项', iconComponent: markRaw(Opportunity), placeholder: '请选择工程项目', supports_required: true, supports_options: false, supports_print: true },
  {
    key: 'leave_type_balance',
    field_type: 'select',
    label: '请假类型',
    icon: '假',
    iconComponent: markRaw(Calendar),
    default_code: 'leave_type',
    placeholder: '请选择',
    supports_required: true,
    supports_options: false,
    supports_print: true,
    required_by_default: true,
    options_payload: {
      data_source: 'leave_balance',
      attendance_component: 'leave',
      value_field: 'leave_type_name',
      label_field: 'option_label',
    },
  },
  { key: 'ocr_text', field_type: 'ocr_text', label: '通用文字识别', icon: '识', iconComponent: markRaw(Memo), placeholder: '识别文字', supports_required: false, supports_options: false, supports_print: true, disabled: true, disabled_reason: '高级识别控件需开通后使用' },
  { key: 'id_card_ocr', field_type: 'id_card_ocr', label: '身份证识别', icon: '证', iconComponent: markRaw(Postcard), placeholder: '识别身份证', supports_required: false, supports_options: false, supports_print: true, disabled: true, disabled_reason: '高级识别控件需开通后使用' },
  { key: 'serial_number', field_type: 'serial_number', label: '流水号', icon: '号', iconComponent: markRaw(CollectionTag), placeholder: '自动生成', supports_required: false, supports_options: false, supports_print: true, readonly_by_default: true, disabled: true, disabled_reason: '流水号控件需开通增强能力后使用' },
]
const attendanceControlDefinitions: ControlDefinition[] = [
  {
    key: 'leave_component',
    field_type: 'attendance_component',
    label: '请假',
    icon: '假',
    placeholder: '请选择',
    supports_required: false,
    supports_options: false,
    supports_print: true,
    attendance_component: true,
    component_type: 'leave',
    component_fields: [
      { label: '请假类型', code: 'leave_type', field_type: 'select', is_required: true, placeholder: '请选择', printable: true, options_json: { options: ['年假', '婚假', '产休假', '陪产假', '病假', '调休', '事假', '病假（长期）', '产检假', '丧假', '病假（通用）'], attendance_component: 'leave' } },
      { label: '开始时间', code: 'start_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'leave' } },
      { label: '结束时间', code: 'end_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'leave' } },
      { label: '请假时长', code: 'leave_duration', field_type: 'duration', is_required: true, placeholder: '0天', printable: true, is_business_calculation: true, is_readonly: true, options_json: { attendance_component: 'leave', time_scale: 'day', unit_hours: 24, duration_mode: 'natural_day' } },
    ],
  },
  {
    key: 'outside_component',
    field_type: 'attendance_component',
    label: '外出',
    icon: '外',
    placeholder: '请选择',
    supports_required: false,
    supports_options: false,
    supports_print: true,
    attendance_component: true,
    component_type: 'outside',
    component_fields: [
      { label: '开始时间', code: 'outside_start_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'outside' } },
      { label: '结束时间', code: 'outside_end_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'outside' } },
      { label: '外出时长', code: 'outside_duration', field_type: 'duration', is_required: true, placeholder: '0天', printable: true, is_business_calculation: true, is_readonly: true, options_json: { attendance_component: 'outside', time_scale: 'day', unit_hours: 24, duration_mode: 'natural_day' } },
    ],
  },
  {
    key: 'business_trip_component',
    field_type: 'attendance_component',
    label: '出差',
    icon: '差',
    placeholder: '请选择',
    supports_required: false,
    supports_options: false,
    supports_print: true,
    attendance_component: true,
    component_type: 'business_trip',
    component_fields: [
      { label: '出差时长', code: 'trip_duration', field_type: 'duration', is_required: true, placeholder: '0天', printable: true, is_business_calculation: true, is_readonly: true, options_json: { attendance_component: 'business_trip', time_scale: 'day', unit_hours: 24, duration_mode: 'natural_day' } },
      {
        label: '明细',
        code: 'trip_expense_detail',
        field_type: 'detail',
        is_required: true,
        placeholder: '添加明细',
        printable: true,
        is_business_calculation: true,
        options_json: {
          child_fields: [
            { label: '机票', code: 'flight_ticket', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '火车票', code: 'train_ticket', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '住宿', code: 'lodging', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '出差补助', code: 'trip_allowance', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '市内交通', code: 'local_transport', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
          ],
          summary: {
            enabled: true,
            label: '预计出差费用合计',
            field_codes: ['flight_ticket', 'train_ticket', 'lodging', 'trip_allowance', 'local_transport'],
            value_type: 'amount',
          },
          print_layout: 'multi_line',
        },
      },
    ],
  },
  {
    key: 'overtime_component',
    field_type: 'attendance_component',
    label: '加班',
    icon: '班',
    placeholder: '请选择',
    supports_required: false,
    supports_options: false,
    supports_print: true,
    attendance_component: true,
    component_type: 'overtime',
    component_fields: [
      { label: '开始时间', code: 'overtime_start_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'overtime' } },
      { label: '结束时间', code: 'overtime_end_time', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'overtime' } },
      { label: '加班时长', code: 'overtime_duration', field_type: 'duration', is_required: true, placeholder: '0天', printable: true, is_business_calculation: true, is_readonly: true, options_json: { attendance_component: 'overtime', time_scale: 'day', unit_hours: 8, duration_mode: 'workday', follows_attendance_overtime_rule: true } },
    ],
  },
  {
    key: 'shift_component',
    field_type: 'attendance_component',
    label: '调班',
    icon: '调',
    placeholder: '请选择',
    supports_required: false,
    supports_options: false,
    supports_print: true,
    attendance_component: true,
    component_type: 'shift',
    component_fields: [
      { label: '原班次日期', code: 'source_shift_date', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'shift' } },
      { label: '调整至日期', code: 'target_shift_date', field_type: 'date', is_required: true, placeholder: '请选择', printable: true, options_json: { attendance_component: 'shift' } },
      { label: '调班原因', code: 'shift_reason', field_type: 'textarea', is_required: false, placeholder: '请输入', printable: true, options_json: { attendance_component: 'shift' } },
    ],
  },
  {
    key: 'punch_component',
    field_type: 'attendance_component',
    label: '补卡',
    icon: '补',
    placeholder: '请选择',
    supports_required: false,
    supports_options: false,
    supports_print: true,
    attendance_component: true,
    component_type: 'punch_correction',
    component_fields: [
      { label: '补卡日期', code: 'correction_date', field_type: 'date', is_required: true, placeholder: '请选择日期', printable: true, options_json: { attendance_component: 'punch_correction' } },
      { label: '补卡班次', code: 'punch_correction_slot', field_type: 'select', is_required: false, placeholder: '请选择', printable: true, display_condition: 'source=attendance_abnormal', options_json: { attendance_component: 'punch_correction', context_only: true, options: ['上班卡', '下班卡'] } },
      { label: '补卡时间', code: 'punch_time', field_type: 'datetime', is_required: true, placeholder: '请选择时间', printable: true, options_json: { attendance_component: 'punch_correction' } },
      { label: '补卡事由', code: 'reason', field_type: 'textarea', is_required: true, placeholder: '请输入', printable: true, options_json: { attendance_component: 'punch_correction' } },
      { label: '说明附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true, options_json: { attendance_component: 'punch_correction' } },
    ],
  },
]
const allControlDefinitions = [...commonControlDefinitions, ...attendanceControlDefinitions]
const controlPaneTabs: Array<{ value: ControlPane; label: string; icon: Component }> = [
  { value: 'controls', label: '控件', icon: markRaw(Operation) },
  { value: 'groups', label: '控件组', icon: markRaw(Collection) },
  { value: 'relations', label: '关联', icon: markRaw(Switch) },
]
const controlDefinitionMap = new Map(commonControlDefinitions.map((control) => [control.key, control]))
const relationControlKeys = new Set([
  'member',
  'department',
  'company',
  'external_contact',
  'industry_department',
  'related_approval',
  'collection_account',
  'invoice',
  'customer',
  'budget_request',
  'related_contract',
  'engineering_project',
])
const controlLibraryCategories: ControlLibraryCategory[] = [
  { key: 'layout', title: '布局控件', controls: ['layout_column', 'divider'].map((key) => controlDefinitionMap.get(key)).filter(Boolean) as ControlDefinition[] },
  { key: 'basic', title: '基础控件', controls: ['text', 'textarea', 'number', 'radio', 'checkbox', 'select', 'date', 'datetime', 'date_range', 'duration', 'static_text', 'identity_card', 'phone'].map((key) => controlDefinitionMap.get(key)).filter(Boolean) as ControlDefinition[] },
  { key: 'enhanced', title: '增强控件', controls: ['cascade', 'ai_control', 'image', 'detail', 'amount', 'attachment', 'signature', 'external_contact', 'member', 'department', 'company', 'industry_department', 'location', 'formula', 'related_approval', 'province_city', 'rating'].map((key) => controlDefinitionMap.get(key)).filter(Boolean) as ControlDefinition[] },
  { key: 'business', title: '业务控件', controls: ['trip_itinerary_detail', 'trip_expense_detail', 'expense_detail', 'payment_collection_detail', 'leave_type_balance', 'invoice', 'customer', 'collection_account', 'budget_request', 'related_contract', 'engineering_project'].map((key) => controlDefinitionMap.get(key)).filter(Boolean) as ControlDefinition[] },
  { key: 'advanced', title: '高级控件', controls: ['ocr_text', 'id_card_ocr', 'serial_number'].map((key) => controlDefinitionMap.get(key)).filter(Boolean) as ControlDefinition[], note: '以下功能属于表单增强能力' },
]
const activeControlPaneLabel = computed(() => controlPaneTabs.find((pane) => pane.value === activeControlPane.value)?.label || '控件')
const paletteControlCategories = computed<ControlLibraryCategory[]>(() => {
  const categories = activeControlPane.value === 'groups'
    ? [{ key: 'attendance', title: '假勤组件', controls: attendanceControlDefinitions, note: '一个模板仅支持添加一种假勤组件' }]
    : activeControlPane.value === 'relations'
      ? [{ key: 'relation', title: '关联控件', controls: commonControlDefinitions.filter((control) => relationControlKeys.has(control.key)) }]
      : controlLibraryCategories
  return categories
    .map((category) => ({
      ...category,
      controls: category.controls.filter((control) => {
        if (!detailChildTargetCode.value) return true
        return control.field_type !== 'detail'
          && control.field_type !== 'layout_column'
          && !control.attendance_component
          && !control.component_fields?.length
      }),
    }))
    .filter((category) => category.controls.length)
})
const fieldTypeOptions = [
  ...commonControlDefinitions.filter((control) => !control.disabled).map((control) => ({ label: control.label, value: control.field_type })),
  { label: '下拉选择', value: 'select' },
  { label: '开关', value: 'boolean' },
].filter((option, index, source) => source.findIndex((item) => item.value === option.value) === index)
const legacyTemplateFieldTypeAliases: Record<string, string> = {
  business_trip_duration: 'duration',
  single_select: 'select',
}
const supportedTemplateFieldTypes = new Set([
  ...fieldTypeOptions.map((option) => option.value),
  'int',
  'float',
])
const detailSummaryFieldTypes = new Set(['number', 'int', 'float', 'amount', 'formula'])
const businessTripExpenseFieldCodes = ['flight_ticket', 'train_ticket', 'lodging', 'trip_allowance', 'local_transport']
const selectedFieldTypeOptions = computed(() => {
  if (!selectedDetailChildCode.value) return fieldTypeOptions
  return fieldTypeOptions.filter((option) => option.value !== 'detail')
})

const records = computed(() => approvalArchiveRows.value)
const templateSortDialogTitle = computed(() => (
  templateSortMode.value === 'groups' ? '分组排序' : `${templateSortGroupName.value}表单排序`
))

function normalizeTemplateLookup(value: unknown) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '_')
}

function isAttendanceRuleBindingTemplate(input: { business_code?: unknown }) {
  return normalizeTemplateLookup(input.business_code).startsWith('attendance_rule_')
}

function templateCategoryNameFromRaw(value: unknown) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  const normalized = normalizeTemplateLookup(raw)
  const matched = templateCategoryDefinitions.find((category) => (
    category.name === raw
    || normalizeTemplateLookup(category.key) === normalized
    || category.aliases.map(normalizeTemplateLookup).includes(normalized)
  ))
  return matched?.name || raw
}

function templateCategoryNameForItem(item: Partial<TemplateMenuItem>) {
  if (item.id && item.is_active === false) return '已停用'
  return templateCategoryNameFromRaw(item.category) || '售后现场'
}

function templatePersistedSortOrder(item: TemplateMenuItem) {
  return item.id && Number.isFinite(Number(item.sort_order)) ? Number(item.sort_order) : Number.POSITIVE_INFINITY
}

function templateGroupSortOrder(group: TemplateGroup) {
  const orders = group.items
    .map(templatePersistedSortOrder)
    .filter((order) => Number.isFinite(order))
  return orders.length ? Math.min(...orders) : Number.POSITIVE_INFINITY
}

function sortTemplateGroups(groups: TemplateGroup[]) {
  return [...groups].sort((a, b) => {
    const persistedA = templateGroupSortOrder(a)
    const persistedB = templateGroupSortOrder(b)
    if (Number.isFinite(persistedA) || Number.isFinite(persistedB)) {
      if (persistedA !== persistedB) return persistedA - persistedB
    }
    const orderA = templateCategoryOrder.get(a.name) ?? 100
    const orderB = templateCategoryOrder.get(b.name) ?? 100
    if (orderA !== orderB) return orderA - orderB
    return a.name.localeCompare(b.name, 'zh-Hans-CN')
  })
}

function groupTemplateItems(items: TemplateMenuItem[]) {
  const grouped = new Map<string, TemplateMenuItem[]>()
  for (const item of items) {
    const groupName = templateCategoryNameForItem(item)
    grouped.set(groupName, [...(grouped.get(groupName) || []), item])
  }
  return sortTemplateGroups(Array.from(grouped.entries()).map(([name, groupItems]) => ({
    name,
    items: [...groupItems].sort((a, b) => {
      if (name === '人事') {
        const orderA = videoHrTemplateOrder.get(catalogItemForTemplate(a)?.name || a.name) ?? Number.POSITIVE_INFINITY
        const orderB = videoHrTemplateOrder.get(catalogItemForTemplate(b)?.name || b.name) ?? Number.POSITIVE_INFINITY
        if (orderA !== orderB) return orderA - orderB
      }
      const activeWeightA = a.id && a.is_active === false ? 1 : 0
      const activeWeightB = b.id && b.is_active === false ? 1 : 0
      if (activeWeightA !== activeWeightB) return activeWeightA - activeWeightB
      const orderA = a.id ? 0 : 1
      const orderB = b.id ? 0 : 1
      if (orderA !== orderB) return orderA - orderB
      const sortOrderA = templatePersistedSortOrder(a)
      const sortOrderB = templatePersistedSortOrder(b)
      if (sortOrderA !== sortOrderB) return sortOrderA - sortOrderB
      return a.name.localeCompare(b.name, 'zh-Hans-CN')
    }),
  })))
}

function catalogItemForTemplate(input: { name?: unknown; business_code?: unknown }) {
  const name = String(input.name || '').trim()
  const businessCode = normalizeTemplateLookup(input.business_code)
  return approvalTemplateCatalog.find((item) => {
    const names = [item.name, ...(item.nameAliases || [])]
    const codes = [item.business_code, ...(item.codeAliases || [])].map(normalizeTemplateLookup)
    return names.includes(name) || (!!businessCode && codes.includes(businessCode))
  }) || null
}

function placeholderTemplateItem(catalogItem: ApprovalTemplateCatalogItem): TemplateMenuItem {
  return {
    ...catalogItem,
    scope: '未创建',
    note: catalogItem.note || '点击初始化模板',
    category: catalogItem.category,
    is_active: undefined,
  }
}

function templateMenuItemFromApi(item: any): TemplateMenuItem | null {
  const catalogItem = catalogItemForTemplate({
    name: item?.name,
    business_code: item?.business_code,
  })
  const name = catalogItem?.name || String(item?.name || '').trim()
  if (!name) return null
  const description = String(item?.description || '').trim()
  const businessCode = item?.business_code || catalogItem?.business_code || templateBusinessCodeForName(name)
  const rawCategory = item?.category || catalogItem?.category
  const rawCategoryKey = item?.category_key || ''
  const category = templateCategoryNameForItem({
    id: item?.id,
    name,
    business_code: businessCode,
    category: rawCategory,
    categoryKey: rawCategoryKey || catalogItem?.categoryKey,
    is_active: item?.is_active !== false,
  })
  const iconKey = item?.icon_key || catalogItem?.iconKey || (typeof item?.icon === 'string' ? item.icon : '')
  const iconTone = item?.icon_tone || catalogItem?.iconTone || catalogItem?.color || templateColorForCategory(category)
  return {
    id: item.id,
    name,
    scope: item.scope || catalogItem?.scope || 'mobile',
    color: templateColorForTone(iconTone, category),
    category,
    categoryKey: rawCategoryKey || templateCategoryKeyForPayload(category, catalogItem) || '',
    icon: catalogItem?.icon || templateIconForKey(iconKey),
    iconKey,
    iconTone,
    more: true,
    disabled: item.is_active === false,
    focused: catalogItem?.focused,
    business_code: businessCode,
    note: description || catalogItem?.note || '',
    description,
    status: item.status,
    version: Number(item.version || 1),
    created_at: item.created_at,
    updated_at: item.updated_at,
    sort_order: Number(item.sort_order || 0),
    is_active: item.is_active !== false,
  }
}

function templateColorForCategory(category: string) {
  if (category === '假勤管理') return 'blue'
  if (category === '人事管理') return 'green'
  if (category === '智能财务') return 'orange'
  if (category === '法务管理') return 'purple'
  if (category === '行政管理') return 'teal'
  if (category === '业务管理') return 'cyan'
  return 'gold'
}

const templateIconComponentByKey: Record<string, Component> = {
  'alarm-clock': markRaw(AlarmClock),
  avatar: markRaw(Avatar),
  box: markRaw(Box),
  briefcase: markRaw(Briefcase),
  calendar: markRaw(Calendar),
  'circle-check': markRaw(CircleCheck),
  'circle-close': markRaw(CircleClose),
  clock: markRaw(LocationFilled),
  coin: markRaw(Coin),
  collection: markRaw(Collection),
  'collection-tag': markRaw(CollectionTag),
  'credit-card': markRaw(CreditCard),
  'data-board': markRaw(DataBoard),
  document: markRaw(Document),
  'document-checked': markRaw(DocumentChecked),
  'document-delete': markRaw(DocumentDelete),
  folder: markRaw(Folder),
  goods: markRaw(Goods),
  key: markRaw(Key),
  memo: markRaw(Memo),
  money: markRaw(Money),
  'office-building': markRaw(OfficeBuilding),
  operation: markRaw(Operation),
  opportunity: markRaw(Opportunity),
  position: markRaw(Position),
  postcard: markRaw(Postcard),
  'price-tag': markRaw(PriceTag),
  reading: markRaw(Reading),
  refresh: markRaw(Refresh),
  service: markRaw(Service),
  'shopping-bag': markRaw(ShoppingBag),
  'shopping-cart': markRaw(ShoppingCart),
  stamp: markRaw(Stamp),
  suitcase: markRaw(Suitcase),
  switch: markRaw(Switch),
  'takeaway-box': markRaw(TakeawayBox),
  tickets: markRaw(Tickets),
  tools: markRaw(Tools),
  unlock: markRaw(Unlock),
  user: markRaw(User),
  'user-filled': markRaw(UserFilled),
  van: markRaw(Van),
  wallet: markRaw(Wallet),
  'wallet-filled': markRaw(WalletFilled),
}

function templateIconForKey(iconKey?: string | null) {
  const normalized = String(iconKey || '').trim()
  return templateIconComponentByKey[normalized] || Calendar
}

function templateColorForTone(iconTone?: string | null, fallbackCategory?: string) {
  const tone = String(iconTone || '').trim()
  if (['blue', 'green', 'orange', 'purple', 'teal', 'cyan', 'gold', 'yellow'].includes(tone)) {
    return tone
  }
  return fallbackCategory ? templateColorForCategory(fallbackCategory) : 'blue'
}

function mergeAllowedTemplateItems(groups: any[]) {
  const savedItems = new Map<string, TemplateMenuItem>()
  const customItems: TemplateMenuItem[] = []
  for (const group of groups || []) {
    const templates = Array.isArray(group?.templates) ? group.templates : []
    for (const item of templates) {
      if (isAttendanceRuleBindingTemplate(item)) continue
      const menuItem = templateMenuItemFromApi(item)
      if (!menuItem) continue
      const catalogItem = catalogItemForTemplate(menuItem)
      if (catalogItem) {
        savedItems.set(catalogItem.name, menuItem)
      } else {
        customItems.push(menuItem)
      }
    }
  }
  return [
    ...approvalTemplateCatalog.map((catalogItem) => savedItems.get(catalogItem.name) || placeholderTemplateItem(catalogItem)),
    ...customItems,
  ]
}

function syncTemplateGroupUiState(groups: TemplateGroup[]) {
  const nextExpanded = { ...expandedTemplateGroups.value }
  for (const group of groups) {
    if (nextExpanded[group.name] === undefined) {
      nextExpanded[group.name] = true
    }
  }
  expandedTemplateGroups.value = nextExpanded
  if (!groups.some((group) => group.name === activeTemplateCategory.value)) {
    activeTemplateCategory.value = groups[0]?.name || '人事'
  }
  const availableIds = new Set(groups.flatMap((group) => group.items).map((item) => item.id).filter((id): id is number => typeof id === 'number'))
  selectedTemplateIds.value = selectedTemplateIds.value.filter((id) => availableIds.has(id))
  if (!selectedTemplateIds.value.length) {
    templateBatchMode.value = false
  }
}

function setTemplateGroupsFromItems(items: TemplateMenuItem[]) {
  const groups = groupTemplateItems(items)
  templateGroups.value = groups
  syncTemplateGroupUiState(groups)
}

function isTemplateGroupExpanded(groupName: string) {
  return expandedTemplateGroups.value[groupName] !== false
}

function isControlCategoryExpanded(categoryKey: string) {
  return expandedControlCategories.value[categoryKey] !== false
}

function toggleControlCategory(categoryKey: string) {
  expandedControlCategories.value = {
    ...expandedControlCategories.value,
    [categoryKey]: !isControlCategoryExpanded(categoryKey),
  }
}

function controlIconComponent(control: ControlDefinition) {
  return control.iconComponent || null
}

function toggleTemplateGroup(groupName: string) {
  expandedTemplateGroups.value = {
    ...expandedTemplateGroups.value,
    [groupName]: !isTemplateGroupExpanded(groupName),
  }
  activeTemplateCategory.value = groupName
}

function focusTemplateCategory(groupName: string) {
  activeTemplateCategory.value = groupName
  expandedTemplateGroups.value = {
    ...expandedTemplateGroups.value,
    [groupName]: true,
  }
  void nextTick(() => {
    updateFormManagerStickyHeight()
    const target = Array.from(document.querySelectorAll<HTMLElement>('.template-group-panel'))
      .find((element) => element.dataset.templateGroup === groupName)
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

function isTemplateSelected(item: TemplateMenuItem) {
  return Boolean(item.id && selectedTemplateIds.value.includes(item.id))
}

function toggleTemplateSelection(item: TemplateMenuItem, event: Event) {
  if (!item.id) return
  const checked = (event.target as HTMLInputElement).checked
  selectedTemplateIds.value = checked
    ? Array.from(new Set([...selectedTemplateIds.value, item.id]))
    : selectedTemplateIds.value.filter((id) => id !== item.id)
}

function toggleTemplateSelectionByItem(item: TemplateMenuItem) {
  if (!item.id) return
  selectedTemplateIds.value = isTemplateSelected(item)
    ? selectedTemplateIds.value.filter((id) => id !== item.id)
    : Array.from(new Set([...selectedTemplateIds.value, item.id]))
}

function exitTemplateBatchMode() {
  templateBatchMode.value = false
  selectedTemplateIds.value = []
}

function templateScopeText(item: TemplateMenuItem) {
  if (!item.id) return '待初始化'
  if (item.is_active === false) return '已停用'
  const scope = String(item.scope || '').trim()
  if (!scope || scope === 'mobile' || scope === 'all' || scope === '全公司可见') return '全部可见'
  if (scope === 'department') return '指定部门可见'
  return scope
}

function templateUpdatedText(item: TemplateMenuItem) {
  if (!item.id) return '未创建'
  const updatedAt = item.updated_at || item.created_at
  if (!updatedAt) return '更新时间未知'
  return `更新于 ${formatApprovalDateTime(updatedAt)}`
}

function templateStatusLabel(item: TemplateMenuItem) {
  if (!item.id) return '待创建'
  if (item.is_active === false) return '已停用'
  if (item.status === 'draft') return '草稿'
  return '启用中'
}

function templateStateClass(item: TemplateMenuItem) {
  if (!item.id) return 'template-state--draft'
  if (item.is_active === false) return 'template-state--disabled'
  if (item.status === 'draft') return 'template-state--draft'
  return 'template-state--active'
}

function templateIconForName(name: string) {
  return catalogItemForTemplate({ name })?.icon || Calendar
}

function templateColorForName(name: string) {
  return catalogItemForTemplate({ name })?.color || 'blue'
}

const currentTemplateCatalogItem = computed(() => catalogItemForTemplate({ name: currentTemplate.name }))
const currentTemplateIcon = computed(() => templateIconForName(currentTemplate.name))
const currentTemplateColor = computed(() => templateColorForName(currentTemplate.name))
const editorSaveStateText = computed(() => {
  const state = currentTemplate.status === 'draft' || !currentTemplate.id ? '草稿' : '已发布'
  return rulesDirty.value ? '有未发布修改' : state
})
const editorSteps: Array<{ value: EditorStep; label: string }> = [
  { value: 'basic', label: '基础设置' },
  { value: 'form', label: '表单设计' },
  { value: 'flow', label: '流程设计' },
]
const selectedField = computed(() => {
  const field = editableFields.value.find((item) => item.code === selectedFieldCode.value) || null
  if (field && selectedDetailChildCode.value) {
    return detailChildFields(field).find((child) => child.code === selectedDetailChildCode.value) || field
  }
  return field
})
const selectedFieldControlDefinition = computed(() => (
  selectedField.value ? controlDefinitionForType(selectedField.value.field_type) : undefined
))
const selectedFieldTypeName = computed(() => (
  selectedField.value ? fieldTypeLabel(selectedField.value.field_type) : '控件设置'
))
const selectedFieldIconComponent = computed(() => selectedFieldControlDefinition.value?.iconComponent)
const selectedFieldIconText = computed(() => selectedFieldControlDefinition.value?.icon || 'Aa')
const canUndoFormEdit = computed(() => false)
const canRedoFormEdit = computed(() => false)
const selectedFieldSupportsSelectionMode = computed(() => {
  const fieldType = selectedField.value?.field_type
  return fieldType === 'member' || fieldType === 'department'
})
const selectedFieldLabelDuplicated = computed(() => {
  const label = selectedField.value?.label?.trim()
  if (!label) return false
  if (!selectedDetailChildCode.value) {
    return editableFields.value.filter((field) => field.label.trim() === label).length > 1
  }
  const parent = editableFields.value.find((field) => field.code === selectedFieldCode.value)
  return detailChildFields(parent).filter((field) => field.label.trim() === label).length > 1
})
const relatedApprovalTemplateOptions = computed(() => (
  approvalTemplateCatalog.map((item) => ({ name: item.name, code: item.business_code }))
))
const selectedRelatedApprovalCodes = computed(() => {
  const codes = selectedField.value?.options_json?.related_template_codes
  return Array.isArray(codes) ? codes.map((code) => String(code)) : []
})
const memberFieldOptions = computed(() => editableFields.value.filter((field) => normalizeTemplateFieldType(field.field_type, field.code) === 'member'))
const departmentFieldOptions = computed(() => editableFields.value.filter((field) => normalizeTemplateFieldType(field.field_type, field.code) === 'department'))
const designerPreviewFields = computed(() => editableFields.value.filter((field) => !isDesignerPreviewHiddenField(field)))

function normalizeAssigneeSource(source: unknown): AssigneeSource {
  if (
    source === 'department_head'
    || source === 'multi_level_manager'
    || source === 'specific_user'
    || source === 'role'
    || source === 'applicant_self'
    || source === 'applicant_select'
    || source === 'related_member_field'
    || source === 'form_department_head'
  ) {
    return source
  }
  return 'direct_manager'
}

const visibleScopeLabel = computed(() => {
  if (!ruleSettings.visibleDepartmentIds.length) return '全公司'
  const names = departmentOptions.value
    .filter((dept) => ruleSettings.visibleDepartmentIds.includes(dept.id))
    .map((dept) => dept.name)
  const scope = names.join('、') || '已设置'
  return ruleSettings.includeSubDepartments ? `${scope}（含子部门）` : scope
})
const selectedSubmitterLabel = computed(() => {
  const names = memberNamesByIds(ruleSettings.submitMemberIds)
  return names === '未指定' ? '选择成员' : names
})
const submitPermissionLabel = computed(() => {
  if (ruleSettings.submitPermissionType === 'selected_members') {
    const names = memberNamesByIds(ruleSettings.submitMemberIds)
    return names === '未指定' ? '指定成员未选择' : names
  }
  if (ruleSettings.submitPermissionType === 'roles') {
    const labels = ruleSettings.submitRoles
      .map((role) => approvalRoleOptions.find((item) => item.value === role)?.label || role)
      .filter(Boolean)
    return labels.length ? labels.join('、') : '指定角色未选择'
  }
  return '全部员工'
})
const selectedTemplateAdminLabel = computed(() => {
  const names = memberNamesByIds(ruleSettings.templateAdminIds)
  return names === '未指定' ? '选择管理员' : names
})
const ruleDialogTitle = computed(() => {
  if (activeRuleDialog.value === 'visible') return '可见范围'
  if (activeRuleDialog.value === 'submit') return '以下部门和人员可以发起提交'
  if (activeRuleDialog.value === 'templateAdmin') return '请从以下「OA审批」管理员中选择'
  return '规则设置'
})
const flowPreviewText = computed(() => {
  if (!flowNodes.value.length) return '画布已闭环，可继续添加审批节点'
  if (!collectBlockingFlowNodes(flowNodes.value).length) return '画布已闭环，发布前请补充审批节点'
  const unresolved = collectExecutableFlowNodes(flowNodes.value).filter((node) => hasMissingSpecificMember(node))
  return unresolved.length ? '有节点尚未指定成员，提交前请完善' : '流程已配置，可保存发布'
})
const approvalPreviewFields = computed(() => (
  editableFields.value
    .map((field) => normalizeTemplateField({ ...field }))
    .filter((field) => field.field_type !== 'layout_column' && !isDesignerPreviewHiddenField(field))
))
const previewRequiredFieldCount = computed(() => approvalPreviewFields.value.filter((field) => field.is_required).length)
const approvalPreviewFlowNodes = computed(() => collectExecutableFlowNodes(flowNodes.value))
const approvalPreviewFlowEntries = computed(() => buildPreviewFlowEntries(flowNodes.value))
const simulationFlowEntries = computed(() => buildSimulationFlowEntries(flowNodes.value))
const simulationRoleOptions = computed(() => [
  { value: 'employee', label: '员工' },
  ...approvalRoleOptions,
])
const simulatedApplicant = computed(() => (
  memberOptions.value.find((member) => member.id === simulateApplicantId.value) || null
))
const simulationMatchedSummary = computed(() => {
  const matchedBranches = simulationFlowEntries.value.filter((entry) => entry.kind === 'branch' && entry.matched)
  if (!matchedBranches.length) return '当前流程没有条件分支，申请会按顺序流转。'
  return `已命中：${matchedBranches.map((entry) => entry.title).join(' / ')}`
})
const approvalPreviewSubtitle = computed(() => (
  `${submitPermissionLabel.value}可发起，${visibleScopeLabel.value}可见`
))
const visibleFlowVersions = computed(() => flowVersions.value.slice(0, 6))
const latestFlowVersion = computed(() => flowVersions.value[0] || null)
const selectedPublishedFlowVersion = computed(() => (
  flowVersions.value.find((version) => version.id === selectedFlowVersionId.value) || latestFlowVersion.value
))
const flowVersionTriggerLabel = computed(() => (
  currentFlowVersionMode.value === 'draft'
    ? '未发布版本'
    : `流程版本V${selectedPublishedFlowVersion.value?.version || currentTemplate.version || 1}`
))
const flowVersionStatusLabel = computed(() => {
  if (currentFlowVersionMode.value === 'draft') return '设计中'
  return selectedPublishedFlowVersion.value?.id === latestFlowVersion.value?.id ? '启用中' : '已发布'
})
const flowVersionStatusTone = computed(() => (currentFlowVersionMode.value === 'draft' ? 'draft' : 'active'))
const draftVersionMetaText = computed(() => {
  if (rulesDirty.value) return '我 本地未发布修改'
  if (draftTemplateSnapshot.value) return '我 已保留未发布草稿'
  return '当前没有未发布修改'
})
const flowVersionMetaText = computed(() => {
  if (currentFlowVersionMode.value === 'draft') return draftVersionMetaText.value
  if (latestFlowVersion.value?.published_at) return `${formatFlowVersionTime(latestFlowVersion.value.published_at)}已发布`
  return flowPreviewText.value
})
const selectedFlowNode = computed(() => findFlowNodeByUid(flowNodes.value, selectedFlowNodeUid.value))
const memberPickerMultiple = computed(() => {
  const node = memberPickerTargetNode.value
  if (memberPickerPurpose.value === 'applicant_select_scope') return true
  if (memberPickerPurpose.value === 'multi_level_member') return true
  return Boolean(
    node
    && node.node_type !== 'auto'
    && !isSplitFlowNode(node)
    && (node.assignee_source === 'specific_user' || isNotifySourceSelected(node, 'specific_user')),
  )
})
const activeBranch = computed(() => {
  const node = selectedFlowNode.value
  if (!node || !isSplitFlowNode(node)) return null
  if (isConditionBranchNode(node)) ensureConditionBranches(node)
  if (isParallelBranchNode(node)) ensureParallelBranches(node)
  return node.branches?.find((branch) => branch.uid === node.active_branch_uid) || node.branches?.[0] || null
})
const activeBranchPriority = computed(() => {
  const node = selectedFlowNode.value
  if (!node || !isConditionBranchNode(node) || !activeBranch.value) return 1
  return (node.branches || []).findIndex((branch) => branch.uid === activeBranch.value?.uid) + 1
})
const leaveTypeConditionOptions = computed(() => {
  const options = normalizedFieldOptions(editableFields.value.find((field) => field.code === 'leave_type'))
  return options.length ? options : defaultLeaveTypeConditionOptions
})
const conditionFieldOptions = computed<ConditionFieldOption[]>(() => [
  { label: '发起人', value: 'applicant.scope', valueKind: 'applicant' },
  { label: '时长', value: 'duration', valueKind: 'number' },
  { label: '请假类型', value: 'leave_type', valueKind: 'selection', options: leaveTypeConditionOptions.value },
])
const conditionFieldLabelMap = computed(() => Object.fromEntries(conditionFieldOptions.value.map((item) => [item.value, item.label])))
const memberNameMap = computed(() => Object.fromEntries(memberOptions.value.map((member) => [member.id, member.name])))
const companyNameMap = computed(() => Object.fromEntries(companyOptions.value.map((company) => [company.id, company.short_name || company.name])))
const departmentNameMap = computed(() => Object.fromEntries(departmentOptions.value.map((department) => [department.id, department.name])))
const departmentTreeData = computed(() => buildDepartmentTree(departmentOptions.value))
const applicantOrgScopeTreeData = computed<ApplicantScopeTreeNode[]>(() => {
  const nodes: ApplicantScopeTreeNode[] = []
  const companyTree = buildApplicantCompanyScopeTree()
  if (companyTree.length) {
    nodes.push({ key: 'scope-root-company', label: '公司', children: companyTree })
  }
  const departmentMemberTree = buildApplicantDepartmentMemberScopeTree(departmentTreeData.value)
  if (departmentMemberTree.length) {
    nodes.push({ key: 'scope-root-department-member', label: '部门/人员', children: departmentMemberTree })
  }
  return nodes
})
const memberPickerTreeData = computed<ApplicantScopeTreeNode[]>(() => buildMemberPickerTreeData())
const roleTreeData = computed<RoleTreeNode[]>(() => {
  const roleMap = new Map(approvalRoleOptions.map((role) => [role.value, role]))
  return approvalRoleTreeGroups
    .map((group) => ({
      key: `role-group-${group.key}`,
      label: group.label,
      children: group.roles
        .map((value) => roleMap.get(value))
        .filter((role): role is RoleOption => Boolean(role))
        .map((role) => ({
          key: `role-${role.value}`,
          label: role.label,
          role,
        })),
    }))
    .filter((group) => group.children.length)
})
const filteredApplicantRoleOptions = computed(() => {
  const keyword = applicantScopeKeyword.value.trim().toLowerCase()
  if (!keyword || applicantScopeMode.value !== 'role') return approvalRoleOptions
  return approvalRoleOptions.filter((role) => {
    const label = role.label.toLowerCase()
    const value = role.value.toLowerCase()
    return label.includes(keyword) || value.includes(keyword)
  })
})
const activeRuleMemberTarget = computed<RuleMemberTarget>(() => {
  if (
    activeRuleDialog.value === 'submit'
    || activeRuleDialog.value === 'templateAdmin'
  ) return activeRuleDialog.value
  return ''
})
const filteredPickerMembers = computed(() => filterMembersByDepartmentAndKeyword(pickerDepartmentId.value, memberKeyword.value))
const filteredRuleMembers = computed(() => filterMembersByDepartmentAndKeyword(rulePickerDepartmentId.value, ruleMemberKeyword.value))
const filteredArchiveApplicantMembers = computed(() => (
  filterMembersByDepartmentAndKeyword(archiveApplicantDepartmentId.value, archiveApplicantKeyword.value)
))
const archiveApplicantDisplay = computed(() => memberCompactLabelById(archiveFilters.applicant_id))

function defaultFieldsForTemplate(name: string): TemplateField[] {
  if (name === '未命名模板') {
    return []
  }
  if (name === '打卡补卡' || name.includes('补卡')) {
    return [
      { label: '补卡日期', code: 'correction_date', field_type: 'date', is_required: true, placeholder: '请选择日期', printable: true, options_json: { attendance_component: 'punch_correction' } },
      { label: '补卡班次', code: 'punch_correction_slot', field_type: 'select', is_required: false, placeholder: '请选择', printable: true, display_condition: 'source=attendance_abnormal', options_json: { attendance_component: 'punch_correction', context_only: true, options: ['上班卡', '下班卡'] } },
      { label: '补卡时间', code: 'punch_time', field_type: 'datetime', is_required: true, placeholder: '请选择时间', printable: true, options_json: { attendance_component: 'punch_correction' } },
      { label: '补卡事由', code: 'reason', field_type: 'textarea', is_required: true, placeholder: '请输入', printable: true, options_json: { attendance_component: 'punch_correction' } },
      { label: '说明附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true, options_json: { attendance_component: 'punch_correction' } },
    ]
  }
  if (name.includes('加班')) {
    return [
      { label: '申请人', code: 'applicant', field_type: 'text', is_required: true, placeholder: '请填写', printable: true },
      { label: '申请部门', code: 'department', field_type: 'department', is_required: true, placeholder: '请选择', printable: true },
      { label: '加班事由', code: 'reason', field_type: 'textarea', is_required: true, placeholder: '请详细说明加班事由（包括地点、工作具体内容等）', printable: true },
      { label: '加班时长', code: 'overtime_duration', field_type: 'duration', is_required: true, placeholder: '0小时', printable: true, is_business_calculation: true, is_readonly: true, options_json: { attendance_component: 'overtime', time_scale: 'hour', unit_hours: 8, duration_mode: 'workday', follows_attendance_overtime_rule: true } },
    ]
  }
  if (name === '请假') {
    return [
      { label: '所在公司', code: 'company', field_type: 'company', is_required: true, placeholder: '请选择', printable: true },
      { label: '请假类型', code: 'leave_type', field_type: 'select', is_required: true, placeholder: '请选择', printable: true, options_json: { data_source: 'leave_balance', label_field: 'option_label', value_field: 'leave_type_name', attendance_component: 'leave' } },
      { label: '请假时长', code: 'duration', field_type: 'duration', is_required: true, placeholder: '0小时', printable: true, is_business_calculation: true, is_readonly: true, options_json: { attendance_component: 'leave', time_scale: 'hour', unit_hours: 8, duration_mode: 'workday' } },
      { label: '请假事由', code: 'reason', field_type: 'textarea', is_required: false, placeholder: '请输入请假事由', printable: true },
      { label: '说明附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true },
    ]
  }
  if (name === '出差') {
    return [
      {
        label: '部门',
        code: 'department',
        field_type: 'department',
        is_required: true,
        placeholder: '请选择',
        printable: true,
        options_json: {
          source: 'manual',
          multiple: true,
          options: ['总经办', '财务部', '人事部', '产研部', '销售事业部', '行政部', '项目管理部'],
        },
      },
      { label: '项目名称', code: 'project_name', field_type: 'select', is_required: true, placeholder: '请选择', printable: true, options_json: { options: ['项目一', '项目二', '项目三'] } },
      { label: '出差事由', code: 'trip_reason', field_type: 'textarea', is_required: true, placeholder: '请输入', printable: true },
      { label: '出差地点', code: 'trip_location', field_type: 'text', is_required: true, placeholder: '请输入此行程涉及的所有城市', printable: true },
      {
        label: '出差时长',
        code: 'trip_duration',
        field_type: 'duration',
        is_required: true,
        placeholder: '0天',
        printable: true,
        is_business_calculation: true,
        is_readonly: true,
        options_json: {
          time_scale: 'day',
          unit_hours: 24,
          duration_mode: 'natural_day',
          attendance_sync: true,
        },
      },
      {
        label: '明细',
        code: 'trip_expense_detail',
        field_type: 'detail',
        is_required: true,
        placeholder: '添加明细',
        printable: true,
        is_business_calculation: true,
        options_json: {
          child_fields: [
            { label: '机票', code: 'flight_ticket', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '火车票', code: 'train_ticket', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '住宿', code: 'lodging', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '出差补助', code: 'trip_allowance', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
            { label: '市内交通', code: 'local_transport', field_type: 'amount', is_required: true, placeholder: '请输入', printable: true },
          ],
          summary: {
            enabled: true,
            label: '预计出差费用合计',
            field_codes: ['flight_ticket', 'train_ticket', 'lodging', 'trip_allowance', 'local_transport'],
            value_type: 'amount',
          },
          print_layout: 'multi_line',
        },
      },
      { label: '附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true },
      { label: '说明', code: 'description', field_type: 'textarea', is_required: false, placeholder: '请输入', printable: true },
    ]
  }
  if (name === '外出') {
    return [
      { label: '部门', code: 'department', field_type: 'department', is_required: true, placeholder: '请选择', printable: true },
      { label: '外出事由', code: 'reason', field_type: 'textarea', is_required: true, placeholder: '请输入', printable: true },
      { label: '外出地点', code: 'outside_location', field_type: 'text', is_required: true, placeholder: '请输入', printable: true },
      { label: '外出时长', code: 'outside_duration', field_type: 'duration', is_required: true, placeholder: '0小时', printable: true, is_business_calculation: true, is_readonly: true, options_json: { attendance_component: 'outside', time_scale: 'hour', unit_hours: 8, duration_mode: 'workday' } },
      { label: '附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true },
    ]
  }
  if (name === '离职' || name === '离职申请') {
    return [
      { label: '所在部门', code: 'department', field_type: 'department', is_required: true, placeholder: '请选择', printable: true },
      { label: '职位', code: 'position', field_type: 'text', is_required: false, placeholder: '请填写', printable: true },
      { label: '入职日期', code: 'entry_date', field_type: 'date', is_required: false, placeholder: '请选择', printable: true },
      { label: '申请离职日期', code: 'resignation_apply_date', field_type: 'date', is_required: false, placeholder: '请选择', printable: true },
      { label: '预计离职日期', code: 'expected_resignation_date', field_type: 'date', is_required: true, placeholder: '请选择', printable: true },
      { label: '离职原因', code: 'resignation_reason', field_type: 'textarea', is_required: true, placeholder: '请输入', printable: true },
      { label: '附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true },
    ]
  }
  if (name === '招聘需求') {
    return [
      { label: '招聘部门', code: 'recruitment_department', field_type: 'department', is_required: true, placeholder: '请选择', printable: true },
      { label: '申请人', code: 'applicant', field_type: 'text', is_required: true, placeholder: '请填写', printable: true },
      { label: '申请日期', code: 'apply_date', field_type: 'date', is_required: false, placeholder: '请选择', printable: true },
      { label: '招聘岗位', code: 'position_name', field_type: 'text', is_required: true, placeholder: '请输入', printable: true },
      { label: '拟招聘人数', code: 'headcount', field_type: 'number', is_required: true, placeholder: '请输入', printable: true },
      { label: '期望到岗日期', code: 'expected_arrival_date', field_type: 'date', is_required: false, placeholder: '请选择', printable: true },
      { label: '岗位职责', code: 'responsibilities', field_type: 'textarea', is_required: false, placeholder: '请输入', printable: true },
      { label: '任职要求', code: 'requirements', field_type: 'textarea', is_required: false, placeholder: '请输入', printable: true },
      { label: '附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true },
    ]
  }
  return [
    { label: `${name}事由`, code: 'reason', field_type: 'textarea', is_required: true, placeholder: '请输入', printable: true },
    { label: '开始日期', code: 'start_date', field_type: 'date', is_required: true, placeholder: '请选择', printable: true },
    { label: '附件', code: 'attachment', field_type: 'attachment', is_required: false, placeholder: '上传附件', printable: true },
  ]
}

function videoTemplateCatalogItem(input: { name?: unknown; business_code?: unknown }) {
  const catalogItem = catalogItemForTemplate(input)
  return catalogItem?.category === '人事' ? catalogItem : null
}

function exactVideoTemplateFields(input: { name?: unknown; business_code?: unknown }) {
  const catalogItem = videoTemplateCatalogItem(input)
  return catalogItem ? cloneData(defaultFieldsForTemplate(catalogItem.name)) : null
}

function normalizeFieldsForTemplate(
  input: { name?: unknown; business_code?: unknown },
  fields: Array<Partial<TemplateField> & Record<string, any>>,
) {
  const exactFields = exactVideoTemplateFields(input)
  if (exactFields) return exactFields
  const normalizedFields = fields.map((field: any) => normalizeTemplateField(field))
  const businessCode = canonicalTemplateBusinessCode(input)
  return businessCode === 'business_trip'
    ? normalizeBusinessTripLegacyDetailFields(normalizedFields)
    : normalizedFields
}

function resetEditableFields(name: string) {
  editableFields.value = cloneData(defaultFieldsForTemplate(name))
  selectedFieldCode.value = preferredSelectedFieldCode(name, editableFields.value)
  selectedDetailChildCode.value = ''
  detailChildTargetCode.value = ''
}

function businessTripExpenseDetailField(legacyFields: TemplateField[]): TemplateField {
  const childFields = businessTripExpenseFieldCodes
    .map((code) => legacyFields.find((field) => field.code === code))
    .filter((field): field is TemplateField => Boolean(field))
    .map((field) => ({
      ...field,
      options_json: cloneFieldOptions(field.options_json),
    }))
  return {
    label: '明细',
    code: 'trip_expense_detail',
    field_type: 'detail',
    is_required: childFields.some((field) => field.is_required),
    placeholder: '添加明细',
    printable: true,
    print_visible: true,
    is_business_calculation: true,
    options_json: {
      child_fields: childFields,
      summary: {
        enabled: true,
        label: '预计出差费用合计',
        field_codes: childFields.map((field) => field.code),
        value_type: 'amount',
      },
      print_layout: 'multi_line',
    },
  }
}

function normalizeBusinessTripLegacyDetailFields(fields: TemplateField[]) {
  if (fields.some((field) => field.code === 'trip_expense_detail' && field.field_type === 'detail')) {
    return fields
  }
  const legacyFields = fields.filter((field) => businessTripExpenseFieldCodes.includes(field.code))
  if (!legacyFields.length) {
    return fields
  }
  const detailInsertIndex = fields.findIndex((field) => field.code === 'detail_header' || businessTripExpenseFieldCodes.includes(field.code))
  const normalized = fields.filter((field) => field.code !== 'detail_header' && !businessTripExpenseFieldCodes.includes(field.code))
  normalized.splice(Math.max(detailInsertIndex, 0), 0, businessTripExpenseDetailField(legacyFields))
  return normalized
}

function preferredSelectedFieldCode(templateName: string, fields: TemplateField[]) {
  if (templateName === '请假') {
    return fields.find((field) => field.code === 'leave_type')?.code || fields[0]?.code || ''
  }
  if (templateName === '出差') {
    return fields.find((field) => field.code === 'project_name')?.code || fields[0]?.code || ''
  }
  return fields[0]?.code || ''
}

function selectField(code: string) {
  selectedFieldCode.value = code
  selectedDetailChildCode.value = ''
  if (detailChildTargetCode.value && detailChildTargetCode.value !== code) {
    detailChildTargetCode.value = ''
  }
}

function undoFormEdit() {
  // 当前模板保存依赖后端版本快照，历史回退入口先保持禁用展示。
}

function redoFormEdit() {
  // 当前模板保存依赖后端版本快照，历史重做入口先保持禁用展示。
}

function selectDetailChild(parentCode: string, childCode: string) {
  selectedFieldCode.value = parentCode
  selectedDetailChildCode.value = childCode
}

function removeField(code: string) {
  const index = editableFields.value.findIndex((field) => field.code === code)
  if (index < 0) return
  editableFields.value.splice(index, 1)
  if (selectedFieldCode.value !== code) return
  const fallback = editableFields.value[Math.min(index, editableFields.value.length - 1)]
  selectedFieldCode.value = fallback?.code || ''
  selectedDetailChildCode.value = ''
  if (detailChildTargetCode.value === code) {
    detailChildTargetCode.value = ''
  }
}

function duplicateField(code: string) {
  const index = editableFields.value.findIndex((field) => field.code === code)
  if (index < 0) return
  const source = editableFields.value[index]
  const duplicated: TemplateField = {
    ...source,
    code: uniqueFieldCodeInFields(source.field_type || 'field', editableFields.value),
    label: `${source.label} 副本`.slice(0, 50),
    options_json: cloneFieldOptions(source.options_json),
  }
  editableFields.value.splice(index + 1, 0, duplicated)
  selectedFieldCode.value = duplicated.code
  selectedDetailChildCode.value = ''
}

function fieldAttendanceComponent(field: TemplateField | null | undefined) {
  const component = field?.options_json?.attendance_component
  return typeof component === 'string' ? component : ''
}

function isDesignerPreviewHiddenField(field: TemplateField | null | undefined) {
  if (!field) return false
  return field.options_json?.context_only === true
    || String(field.display_condition || '').trim() === 'source=attendance_abnormal'
}

function currentAttendanceComponent() {
  return editableFields.value.map(fieldAttendanceComponent).find(Boolean) || ''
}

function isControlDisabled(control: ControlDefinition) {
  if (control.disabled) return true
  if (!control.attendance_component || !control.component_type) return false
  const existing = currentAttendanceComponent()
  return Boolean(existing)
}

function controlDisabledReason(control: ControlDefinition) {
  if (control.disabled_reason) return control.disabled_reason
  if (isControlDisabled(control)) return '一个模板仅支持添加一种假勤组件'
  return ''
}

function cloneFieldOptions(options: TemplateField['options_json']) {
  if (!options || typeof options !== 'object') return options
  return JSON.parse(JSON.stringify(options))
}

function normalizeTemplateFieldType(fieldType: unknown, fieldCode?: unknown) {
  const normalizedType = String(fieldType || '').trim()
  if (!normalizedType) return ''
  if (normalizedType === 'multi_select') {
    const normalizedCode = String(fieldCode || '').trim().toLowerCase()
    return normalizedCode.includes('department') ? 'department' : 'checkbox'
  }
  return legacyTemplateFieldTypeAliases[normalizedType] || normalizedType
}

function normalizeTemplateFieldOptions(options: TemplateField['options_json']) {
  const normalized = cloneFieldOptions(options)
  if (!normalized || typeof normalized !== 'object' || Array.isArray(normalized)) {
    return normalized
  }
  if (Array.isArray(normalized.child_fields)) {
    normalized.child_fields = normalized.child_fields.map((child: TemplateField) => normalizeTemplateField(child))
  }
  return normalized as Record<string, any>
}

function normalizeTemplateField(field: Partial<TemplateField> & Record<string, any>): TemplateField {
  const printVisible = field.print_visible !== false && field.printable !== false
  return {
    label: String(field.label || field.code || ''),
    code: normalizeBusinessTripFieldCode(field.code),
    field_type: normalizeTemplateFieldType(field.field_type, field.code),
    is_required: Boolean(field.is_required),
    placeholder: field.placeholder || '',
    display_condition: field.display_condition ? String(field.display_condition) : undefined,
    printable: printVisible,
    print_visible: printVisible,
    options_json: normalizeTemplateFieldOptions(field.options_json),
    is_business_calculation: Boolean(field.is_business_calculation),
    is_readonly: Boolean(field.is_readonly),
  }
}

function detailChildFields(field: TemplateField | null | undefined): TemplateField[] {
  const children = field?.options_json?.child_fields
  return Array.isArray(children) ? children : []
}

function defaultDetailSummaryLabel(field: TemplateField | null | undefined) {
  const label = String(field?.label || '明细').trim() || '明细'
  return `${label}合计`
}

function defaultDetailOptions(field?: TemplateField | null): Record<string, any> {
  return {
    child_fields: field ? detailChildFields(field) : [],
    summary: {
      enabled: true,
      label: defaultDetailSummaryLabel(field),
      field_codes: [],
      value_type: 'amount',
    },
    print_layout: 'single_line',
  }
}

function ensureFieldOptions(field: TemplateField) {
  if (!field.options_json || typeof field.options_json !== 'object' || Array.isArray(field.options_json)) {
    field.options_json = {}
  }
  return field.options_json
}

function ensureDetailChildFields(field: TemplateField) {
  const options = ensureFieldOptions(field)
  if (!Array.isArray(options.child_fields)) {
    options.child_fields = []
  }
  return options.child_fields as TemplateField[]
}

function ensureDetailSummary(field: TemplateField | null | undefined) {
  if (!field || field.field_type !== 'detail') return null
  const options = ensureFieldOptions(field)
  if (!options.summary || typeof options.summary !== 'object' || Array.isArray(options.summary)) {
    options.summary = {
      enabled: true,
      label: defaultDetailSummaryLabel(field),
      field_codes: [],
      value_type: 'amount',
    }
  }
  if (!Array.isArray(options.summary.field_codes)) {
    options.summary.field_codes = []
  }
  if (!String(options.summary.label || '').trim()) {
    options.summary.label = defaultDetailSummaryLabel(field)
  }
  return options.summary as Record<string, any>
}

function detailSummaryEnabled(field: TemplateField | null | undefined) {
  const summary = field?.options_json?.summary
  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) return false
  return summary.enabled === true
}

function detailSummaryLabel(field: TemplateField | null | undefined) {
  const summary = field?.options_json?.summary
  if (summary && typeof summary === 'object' && !Array.isArray(summary)) {
    const label = String(summary.label || '').trim()
    if (label) return label
  }
  return defaultDetailSummaryLabel(field)
}

function numericDetailChildFields(field: TemplateField | null | undefined) {
  return detailChildFields(field).filter((child) => detailSummaryFieldTypes.has(normalizeTemplateFieldType(child.field_type, child.code)))
}

function detailSummaryFieldCodes(field: TemplateField | null | undefined) {
  const summary = field?.options_json?.summary
  return summary && typeof summary === 'object' && Array.isArray(summary.field_codes)
    ? summary.field_codes.map((code: unknown) => String(code || '').trim()).filter(Boolean)
    : []
}

function detailSummaryUsesField(field: TemplateField | null | undefined, code: string) {
  const selectedCodes = detailSummaryFieldCodes(field)
  return !selectedCodes.length || selectedCodes.includes(code)
}

function isDetailChildSelected(parentCode: string, childCode: string) {
  return selectedFieldCode.value === parentCode && selectedDetailChildCode.value === childCode
}

function fieldTypeLabel(fieldType: string) {
  const normalizedType = normalizeTemplateFieldType(fieldType)
  return fieldTypeOptions.find((option) => option.value === normalizedType)?.label || fieldType
}

function openDetailControlLibrary(parentCode: string) {
  const parent = editableFields.value.find((field) => field.code === parentCode)
  if (!parent || parent.field_type !== 'detail') return
  selectedFieldCode.value = parentCode
  selectedDetailChildCode.value = ''
  detailChildTargetCode.value = parentCode
  ElMessage.info('请从左侧控件栏选择子控件')
}

function closeControlLibrary() {
  detailChildTargetCode.value = ''
}

function removeDetailChildField(parentCode: string, childCode: string) {
  const parent = editableFields.value.find((field) => field.code === parentCode)
  if (!parent) return
  const children = ensureDetailChildFields(parent)
  const index = children.findIndex((child) => child.code === childCode)
  if (index < 0) return
  children.splice(index, 1)
  if (isDetailChildSelected(parentCode, childCode)) {
    selectedFieldCode.value = parentCode
    selectedDetailChildCode.value = ''
  }
}

function onFieldDragStart(code: string, event: DragEvent) {
  draggedFieldCode.value = code
  dragOverFieldCode.value = code
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', code)
  }
}

function onFieldDragOver(targetCode: string) {
  if (!draggedFieldCode.value || draggedFieldCode.value === targetCode) return
  dragOverFieldCode.value = targetCode
}

function onFieldDrop(targetCode: string) {
  const fromCode = draggedFieldCode.value
  if (!fromCode || fromCode === targetCode) {
    onFieldDragEnd()
    return
  }
  const fromIndex = editableFields.value.findIndex((field) => field.code === fromCode)
  const targetIndex = editableFields.value.findIndex((field) => field.code === targetCode)
  if (fromIndex < 0 || targetIndex < 0) {
    onFieldDragEnd()
    return
  }
  const [movedField] = editableFields.value.splice(fromIndex, 1)
  editableFields.value.splice(Math.max(targetIndex, 0), 0, movedField)
  onFieldDragEnd()
}

function onFieldDragEnd() {
  draggedFieldCode.value = ''
  dragOverFieldCode.value = ''
}

function onControlDragStart(control: ControlDefinition, event: DragEvent) {
  if (isControlDisabled(control)) {
    event.preventDefault()
    const reason = controlDisabledReason(control)
    if (reason) ElMessage.warning(reason)
    return
  }
  draggedControlKey.value = control.key
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'copy'
    event.dataTransfer.setData('application/x-approval-control', control.key)
    event.dataTransfer.setData('text/plain', control.key)
  }
}

function onControlDragEnd() {
  draggedControlKey.value = ''
}

function onPreviewDrop(event: DragEvent) {
  const controlKey = event.dataTransfer?.getData('application/x-approval-control') || draggedControlKey.value
  if (!controlKey) return
  const control = allControlDefinitions.find((item) => item.key === controlKey)
  if (!control) return
  addControl(control)
  onControlDragEnd()
}

function fieldPlaceholder(field: TemplateField) {
  const control = controlDefinitionForType(field.field_type)
  if (control) return control.placeholder
  if (field.field_type === 'select') return '请选择'
  if (field.field_type === 'boolean') return '请选择'
  return '请填写'
}

function previewPlaceholder(field: TemplateField) {
  if (field.field_type === 'layout_column') return ''
  if (field.field_type === 'member' || field.field_type === 'department' || field.field_type === 'company' || field.field_type === 'location' || field.field_type === 'related_approval' || field.field_type === 'external_contact' || field.field_type === 'industry_department' || field.field_type === 'province_city' || field.field_type === 'invoice' || field.field_type === 'customer' || field.field_type === 'budget_request' || field.field_type === 'related_contract' || field.field_type === 'engineering_project') {
    return `${field.placeholder || '请选择'} ›`
  }
  if (field.field_type === 'date' || field.field_type === 'datetime' || field.field_type === 'date_range' || field.field_type === 'select' || field.field_type === 'cascade' || field.field_type === 'radio' || field.field_type === 'checkbox' || field.field_type === 'rating') {
    return field.placeholder || '请选择'
  }
  if (field.field_type === 'image' || field.field_type === 'signature') return field.placeholder || '上传图片'
  return field.placeholder || fieldPlaceholder(field)
}

function openApprovalPreview() {
  approvalPreviewDevice.value = formPreviewDevice.value
  approvalPreviewVisible.value = true
}

function closeApprovalPreview() {
  approvalPreviewVisible.value = false
}

function previewFieldKind(field: TemplateField) {
  const normalizedType = normalizeTemplateFieldType(field.field_type, field.code)
  if (['textarea', 'static_text', 'detail', 'attachment', 'image', 'signature'].includes(normalizedType)) return 'block'
  if (normalizedType === 'duration' || normalizedType === 'date_range') return 'compound'
  return 'row'
}

function previewFieldValue(field: TemplateField) {
  const normalizedType = normalizeTemplateFieldType(field.field_type, field.code)
  if (normalizedType === 'static_text') return staticTextContent(field) || '说明文字'
  if (normalizedType === 'duration') return durationPreviewValue(field)
  if (normalizedType === 'date_range') return '开始日期 至 结束日期'
  if (normalizedType === 'detail') {
    const childCount = detailChildFields(field).length
    return childCount ? `${childCount} 个明细列` : '添加明细'
  }
  if (['attachment', 'image', 'signature'].includes(normalizedType)) return field.placeholder || '上传附件'
  if (['select', 'radio', 'checkbox', 'cascade'].includes(normalizedType)) {
    return normalizedFieldOptions(field)[0] || field.placeholder || '请选择'
  }
  if (['member', 'department', 'company', 'location', 'related_approval'].includes(normalizedType)) {
    return field.placeholder || '请选择'
  }
  if (normalizedType === 'phone') return `${controlOption(field, 'country_code', '+86')} ${field.placeholder || '请输入'}`
  if (['date', 'datetime'].includes(normalizedType)) return field.placeholder || '请选择'
  if (normalizedType === 'amount') return '0.00'
  if (normalizedType === 'number') return '0'
  return field.placeholder || '请输入'
}

function previewNodeIcon(node: FlowNodeEditor) {
  if (node.node_type === 'notify') return '抄'
  if (node.node_type === 'handler') return '办'
  if (node.node_type === 'condition_branch') return '分'
  if (node.node_type === 'parallel_branch') return '并'
  if (node.node_type === 'auto') return '自'
  return '审'
}

function flowPreviewEntryForNode(node: FlowNodeEditor, depth: number, matched = false): FlowPreviewEntry {
  return {
    key: `node-${node.uid}-${depth}`,
    kind: 'node',
    icon: previewNodeIcon(node),
    title: nodeTypeLabel(node.node_type),
    subtitle: nodeAssigneeDisplay(node),
    depth,
    matched,
  }
}

function flowPreviewEntryForCondition(node: FlowNodeEditor, depth: number, subtitle?: string): FlowPreviewEntry {
  const branchCount = node.branches?.length || 0
  const isParallel = isParallelBranchNode(node)
  return {
    key: `${isParallel ? 'parallel' : 'condition'}-${node.uid}-${depth}`,
    kind: 'condition',
    icon: isParallel ? '并' : '分',
    title: isParallel ? '并行分支' : '条件分支',
    subtitle: subtitle || (isParallel
      ? (branchCount ? `${branchCount} 条分支同时发起` : '多条分支同时执行')
      : (branchCount ? `${branchCount} 条分支，按优先级匹配` : '按条件匹配不同路径')),
    depth,
  }
}

function flowPreviewEntryForBranch(branch: FlowBranchEditor, depth: number, matched = false, parallel = false): FlowPreviewEntry {
  return {
    key: `branch-${branch.uid}-${depth}-${matched ? 'matched' : 'all'}`,
    kind: 'branch',
    icon: parallel ? '并' : (branch.is_default_branch ? '默' : '条'),
    title: branch.label || (parallel ? '并行分支' : (branch.is_default_branch ? '默认条件' : '条件分支')),
    subtitle: parallel ? `${branch.nodes?.length || 0} 个节点` : branchConditionSummary(branch),
    depth,
    matched,
  }
}

function buildPreviewFlowEntries(nodes: FlowNodeEditor[], depth = 0): FlowPreviewEntry[] {
  const entries: FlowPreviewEntry[] = []
  for (const node of nodes) {
    if (!isSplitFlowNode(node)) {
      entries.push(flowPreviewEntryForNode(node, depth))
      continue
    }
    entries.push(flowPreviewEntryForCondition(node, depth))
    for (const branch of node.branches || []) {
      entries.push(flowPreviewEntryForBranch(branch, depth + 1, false, isParallelBranchNode(node)))
      const branchNodes = branch.nodes || []
      if (branchNodes.length) entries.push(...buildPreviewFlowEntries(branchNodes, depth + 2))
    }
  }
  return entries
}

function buildSimulationFlowEntries(nodes: FlowNodeEditor[], depth = 0): FlowPreviewEntry[] {
  const entries: FlowPreviewEntry[] = []
  for (const node of nodes) {
    if (!isSplitFlowNode(node)) {
      entries.push(flowPreviewEntryForNode(node, depth, true))
      continue
    }
    if (isParallelBranchNode(node)) {
      entries.push(flowPreviewEntryForCondition(node, depth))
      for (const branch of node.branches || []) {
        entries.push(flowPreviewEntryForBranch(branch, depth + 1, true, true))
        const branchNodes = branch.nodes || []
        if (branchNodes.length) entries.push(...buildSimulationFlowEntries(branchNodes, depth + 2))
      }
      continue
    }
    const matchedBranch = simulatedMatchedBranch(node)
    entries.push(flowPreviewEntryForCondition(
      node,
      depth,
      matchedBranch ? `命中「${matchedBranch.label || '未命名分支'}」` : '未找到可命中的分支',
    ))
    if (!matchedBranch) continue
    entries.push(flowPreviewEntryForBranch(matchedBranch, depth + 1, true))
    const branchNodes = matchedBranch.nodes || []
    if (branchNodes.length) entries.push(...buildSimulationFlowEntries(branchNodes, depth + 2))
  }
  return entries
}

function branchConditionSummary(branch: FlowBranchEditor) {
  if (branch.is_default_branch) return '其他条件进入此流程'
  const groups = branchConditionGroupsForRead(branch).map(conditionGroupSummary).filter(Boolean)
  return groups.length ? groups.join(' 或 ') : '请配置条件'
}

function branchConditionGroupsForRead(branch: FlowBranchEditor): FlowConditionGroupEditor[] {
  if (branch.condition_groups?.length) return branch.condition_groups
  if (branch.conditions?.length) {
    return [{
      uid: `${branch.uid}-legacy-group`,
      label: '条件组',
      condition_combinator: branch.condition_combinator || 'and',
      conditions: branch.conditions,
    }]
  }
  return []
}

function conditionGroupSummary(group: FlowConditionGroupEditor) {
  const conditions = (group.conditions || []).map(conditionSummary).filter(Boolean)
  return conditions.join(group.condition_combinator === 'or' ? ' 或 ' : ' 且 ')
}

function conditionSummary(condition: FlowConditionEditor) {
  const operator = conditionOperatorLabel(condition.operator)
  const value = conditionValueText(condition)
  return `${conditionFieldLabel(condition.field)} ${operator}${value ? ` ${value}` : ''}`
}

function conditionValueText(condition: FlowConditionEditor) {
  if (condition.operator === 'empty' || condition.operator === 'not_empty') return ''
  if (condition.operator === 'between') {
    return `${conditionBetweenBound(condition, 0) || '-'} 至 ${conditionBetweenBound(condition, 1) || '-'}`
  }
  const kind = conditionFieldKind(condition.field)
  if (kind === 'applicant') {
    const labels = conditionApplicantScopeValues(condition).map(applicantScopeLabel)
    return labels.length ? labels.join('、') : '未指定'
  }
  const values = conditionValueArray(condition)
  if (kind === 'company') return values.map((item) => companyNameMap.value[Number(item)] || `公司#${item}`).join('、')
  if (kind === 'department') return values.map((item) => departmentNameById(Number(item))).join('、')
  if (kind === 'member') return values.map((item) => memberNameById(Number(item) || null)).join('、')
  return values.map((item) => String(item)).join('、')
}

function openSimulationDialog() {
  ensureSimulationDefaults()
  simulateDialogVisible.value = true
}

function ensureSimulationDefaults() {
  if (!simulateApplicantId.value && memberOptions.value[0]?.id) {
    simulateApplicantId.value = memberOptions.value[0].id
  }
  if (!simulateApplicantCompanyId.value && companyOptions.value[0]?.id) {
    simulateApplicantCompanyId.value = companyOptions.value[0].id
  }
  const activeCodes = new Set(approvalPreviewFields.value.map((field) => field.code))
  for (const key of Object.keys(simulationFormValues)) {
    if (!activeCodes.has(key)) delete simulationFormValues[key]
  }
  for (const field of approvalPreviewFields.value) {
    if (simulationFormValues[field.code] === undefined) {
      simulationFormValues[field.code] = simulationDefaultValue(field)
    }
  }
}

function simulationFieldControl(field: TemplateField) {
  const type = normalizeTemplateFieldType(field.field_type, field.code)
  if (['select', 'radio', 'checkbox', 'cascade'].includes(type)) return 'select'
  if (['number', 'int', 'float', 'amount', 'formula', 'duration'].includes(type)) return 'number'
  if (['date', 'datetime', 'date_range'].includes(type)) return 'date'
  return 'text'
}

function simulationFieldOptions(field: TemplateField) {
  const options = normalizedFieldOptions(field)
  return options.length ? options : [previewFieldValue(field)]
}

function simulationDefaultValue(field: TemplateField) {
  const control = simulationFieldControl(field)
  if (control === 'select') return simulationFieldOptions(field)[0] || ''
  if (control === 'number') return 0
  return ''
}

function simulatedMatchedBranch(node: FlowNodeEditor) {
  const branches = node.branches || []
  const conditionalBranches = branches.filter((branch) => !branch.is_default_branch)
  const matched = conditionalBranches.find((branch) => branchMatchesSimulation(branch))
  return matched || branches.find((branch) => branch.is_default_branch) || branches[0] || null
}

function branchMatchesSimulation(branch: FlowBranchEditor) {
  if (branch.is_default_branch) return true
  const groups = branchConditionGroupsForRead(branch)
  if (!groups.length) return false
  const matches = groups.map(conditionGroupMatchesSimulation)
  return branch.condition_group_combinator === 'and'
    ? matches.every(Boolean)
    : matches.some(Boolean)
}

function conditionGroupMatchesSimulation(group: FlowConditionGroupEditor) {
  const conditions = group.conditions || []
  if (!conditions.length) return false
  const matches = conditions.map(conditionMatchesSimulation)
  return group.condition_combinator === 'or'
    ? matches.some(Boolean)
    : matches.every(Boolean)
}

function conditionMatchesSimulation(condition: FlowConditionEditor) {
  const kind = conditionFieldKind(condition.field)
  if (kind === 'applicant') return applicantConditionMatches(condition)
  const actualValue = simulationActualValueForCondition(condition.field)
  return compareConditionValue(actualValue, condition.operator, condition.value, kind)
}

function applicantConditionMatches(condition: FlowConditionEditor) {
  if (condition.operator === 'empty') return !simulatedApplicant.value
  if (condition.operator === 'not_empty') return Boolean(simulatedApplicant.value)
  const scopes = conditionApplicantScopeValues(condition)
  const matched = scopes.some(scopeMatchesSimulatedApplicant)
  return condition.operator === 'not_in' ? !matched : matched
}

function scopeMatchesSimulatedApplicant(scope: ApplicantScopeValue) {
  const applicant = simulatedApplicant.value
  if (scope.type === 'role') return String(scope.value || '') === simulateApplicantRole.value
  if (!applicant) return false
  if (scope.type === 'member') return Number(scope.id) === applicant.id
  if (scope.type === 'department') {
    const scopedDepartmentId = Number(scope.id) || null
    if (!scopedDepartmentId || !applicant.department_id) return false
    return collectDeptAndChildrenIds(scopedDepartmentId).has(Number(applicant.department_id))
  }
  if (scope.type === 'company') return Number(scope.id) === Number(simulateApplicantCompanyId.value)
  return false
}

function simulationActualValueForCondition(field: string) {
  const normalized = normalizeConditionField(field)
  if (normalized === 'duration') {
    const durationField = approvalPreviewFields.value.find((item) => normalizeConditionField(item.code) === 'duration' || normalizeTemplateFieldType(item.field_type, item.code) === 'duration')
    return durationField ? simulationFormValues[durationField.code] : simulationFormValues.duration
  }
  const exactField = approvalPreviewFields.value.find((item) => normalizeConditionField(item.code) === normalized)
  return exactField ? simulationFormValues[exactField.code] : simulationFormValues[normalized]
}

function compareConditionValue(actualValue: any, operator: ConditionOperator, expectedValue: FlowConditionValue, kind: ConditionFieldKind) {
  const empty = actualValue === null || actualValue === undefined || actualValue === ''
  if (operator === 'empty') return empty
  if (operator === 'not_empty') return !empty
  if (kind === 'number') {
    const actualNumber = Number(actualValue)
    if (!Number.isFinite(actualNumber)) return false
    if (operator === 'between') {
      const bounds = Array.isArray(expectedValue) ? expectedValue : []
      const min = Number(bounds[0])
      const max = Number(bounds[1])
      return (!Number.isFinite(min) || actualNumber >= min) && (!Number.isFinite(max) || actualNumber <= max)
    }
    const expectedNumber = Number(expectedValue)
    if (!Number.isFinite(expectedNumber)) return false
    if (operator === 'lt') return actualNumber < expectedNumber
    if (operator === 'lte') return actualNumber <= expectedNumber
    if (operator === 'gt') return actualNumber > expectedNumber
    if (operator === 'gte') return actualNumber >= expectedNumber
    if (operator === 'eq') return actualNumber === expectedNumber
    if (operator === 'neq') return actualNumber !== expectedNumber
  }
  const expectedValues = conditionValueArray({ value: expectedValue } as FlowConditionEditor).map((item) => String(item))
  const actualText = String(actualValue ?? '')
  if (operator === 'in') return expectedValues.includes(actualText)
  if (operator === 'not_in') return !expectedValues.includes(actualText)
  if (operator === 'eq') return actualText === String(expectedValue ?? '')
  if (operator === 'neq') return actualText !== String(expectedValue ?? '')
  if (operator === 'contains') return actualText.includes(String(expectedValue ?? ''))
  if (operator === 'not_contains') return !actualText.includes(String(expectedValue ?? ''))
  return false
}

function durationPreviewValue(field: TemplateField) {
  return controlOption(field, 'time_scale', 'day') === 'hour' ? '0小时' : '0天'
}

function uniqueFieldCodeInFields(base: string, fields: TemplateField[]) {
  let index = fields.length + 1
  let code = `${base}_${index}`
  const used = new Set(fields.map((field) => field.code))
  while (used.has(code)) {
    index += 1
    code = `${base}_${index}`
  }
  return code
}

function uniqueFieldCode(base: string) {
  return uniqueFieldCodeInFields(base, editableFields.value)
}

function uniquePreferredFieldCode(preferredCode: string | undefined, fallbackBase: string) {
  const preferred = String(preferredCode || '').trim()
  const used = new Set(editableFields.value.map((field) => field.code))
  if (preferred && !used.has(preferred)) return preferred
  return uniqueFieldCode(preferred || fallbackBase)
}

function controlDefinitionForType(fieldType: string) {
  return allControlDefinitions.find((control) => control.field_type === fieldType)
}

function optionPayload(controlOrOptions?: ControlDefinition | string[]) {
  if (Array.isArray(controlOrOptions)) {
    return controlOrOptions.length ? { options: [...controlOrOptions] } : undefined
  }
  const control = controlOrOptions
  if (!control) return undefined
  const payload = control.default_options?.length ? { options: [...control.default_options] } : {}
  return control.options_payload
    ? { ...payload, ...cloneData(control.options_payload) }
    : Object.keys(payload).length
      ? payload
      : undefined
}

function fieldFromControl(control: ControlDefinition, code: string): TemplateField {
  const isDetailControl = control.field_type === 'detail'
  const isLayoutControl = control.field_type === 'layout_column'
  const isImageLikeControl = control.field_type === 'image' || control.field_type === 'signature'
  return {
    label: control.label,
    code,
    field_type: control.field_type,
    is_required: Boolean(control.required_by_default),
    placeholder: control.placeholder,
    printable: control.supports_print,
    options_json: isDetailControl
      ? control.options_payload ? cloneData(control.options_payload) : defaultDetailOptions()
      : isLayoutControl
        ? { columns: 2 }
        : isImageLikeControl
          ? { accept: 'image/*' }
          : optionPayload(control),
    is_business_calculation: Boolean(control.business_calculation),
    is_readonly: Boolean(control.readonly_by_default),
  }
}

function addControl(control: ControlDefinition) {
  if (detailChildTargetCode.value) {
    addDetailChildControl(control)
    return
  }
  if (isControlDisabled(control)) {
    const reason = controlDisabledReason(control)
    if (reason) ElMessage.warning(reason)
    return
  }
  if (control.component_fields?.length) {
    const addedFields = control.component_fields.map((source) => {
      const code = editableFields.value.some((field) => field.code === source.code)
        ? uniqueFieldCode(source.code)
        : source.code
      return {
        ...source,
        code,
        options_json: cloneFieldOptions(source.options_json),
      }
    })
    editableFields.value.push(...addedFields)
    selectedFieldCode.value = addedFields[0]?.code || ''
    selectedDetailChildCode.value = ''
    return
  }
  const fieldType = control.field_type
  const field = fieldFromControl(control, uniquePreferredFieldCode(control.default_code, fieldType))
  editableFields.value.push(field)
  selectedFieldCode.value = field.code
  selectedDetailChildCode.value = ''
}

function addDetailChildControl(control: ControlDefinition) {
  if (control.field_type === 'detail' || control.attendance_component || control.component_fields?.length) {
    ElMessage.warning('明细内不支持添加该控件')
    return
  }
  const parent = editableFields.value.find((field) => field.code === detailChildTargetCode.value)
  if (!parent || parent.field_type !== 'detail') {
    closeControlLibrary()
    return
  }
  const children = ensureDetailChildFields(parent)
  const field = fieldFromControl(control, uniqueFieldCodeInFields(control.field_type, children))
  children.push(field)
  selectedFieldCode.value = parent.code
  selectedDetailChildCode.value = field.code
  closeControlLibrary()
}

function fieldUsesDynamicDataSource(field: TemplateField | null | undefined) {
  const dataSource = field?.options_json?.data_source
  return typeof dataSource === 'string' && dataSource.trim() !== ''
}

function fieldSupportsOptions(field: TemplateField | null | undefined) {
  return Boolean(field && !fieldUsesDynamicDataSource(field) && ['select', 'radio', 'checkbox', 'cascade'].includes(field.field_type))
}

function selectedFieldSupportsRequired(field: TemplateField | null | undefined) {
  if (!field) return false
  const control = controlDefinitionForType(field.field_type)
  return control?.supports_required !== false && !field.is_business_calculation && field.field_type !== 'layout_column'
}

function selectedFieldSupportsPrint(field: TemplateField | null | undefined) {
  if (!field) return false
  const control = controlDefinitionForType(field.field_type)
  return control?.supports_print !== false
}

function normalizedFieldOptions(field: TemplateField | null | undefined) {
  if (!field) return []
  const rawOptions = field.options_json?.options
  return Array.isArray(rawOptions) ? rawOptions.map((item) => String(item)) : []
}

function ensureSelectedFieldOptions() {
  if (!selectedField.value) return null
  if (!selectedField.value.options_json || typeof selectedField.value.options_json !== 'object') {
    selectedField.value.options_json = {}
  }
  return selectedField.value.options_json
}

function controlOption(field: TemplateField | null | undefined, key: string, fallback: any) {
  const value = field?.options_json?.[key]
  return value === undefined || value === null || value === '' ? fallback : value
}

function setSelectedControlOption(key: string, value: any) {
  const options = ensureSelectedFieldOptions()
  if (!options) return
  options[key] = value
}

function setSelectedControlOptionFromEvent(key: string, event: Event) {
  const target = event.target as HTMLSelectElement | HTMLInputElement | null
  setSelectedControlOption(key, target?.value)
}

function setSelectedNumericControlOption(key: string, event: Event) {
  const target = event.target as HTMLSelectElement | HTMLInputElement | null
  const value = Number(target?.value)
  setSelectedControlOption(key, Number.isFinite(value) ? value : 0)
}

function setSelectedBooleanControlOption(key: string, event: Event) {
  const target = event.target as HTMLInputElement | null
  setSelectedControlOption(key, Boolean(target?.checked))
}

function setSelectedFieldBoolean(key: 'printable' | 'is_readonly' | 'is_required', event: Event) {
  if (!selectedField.value) return
  const target = event.target as HTMLInputElement | null
  selectedField.value[key] = Boolean(target?.checked)
}

function setSelectedFieldOptions(options: string[]) {
  const payload = ensureSelectedFieldOptions()
  if (!payload) return
  payload.options = options
}

function updateSelectedOption(index: number, event: Event) {
  const target = event.target as HTMLInputElement | null
  const options = normalizedFieldOptions(selectedField.value)
  if (index < 0 || index >= options.length) return
  options[index] = target?.value || ''
  setSelectedFieldOptions(options)
}

function addSelectedOption() {
  const options = normalizedFieldOptions(selectedField.value)
  options.push(`选项${options.length + 1}`)
  setSelectedFieldOptions(options)
}

function addOtherSelectedOption() {
  const options = normalizedFieldOptions(selectedField.value)
  if (!options.includes('其它')) {
    options.push('其它')
    setSelectedFieldOptions(options)
  }
}

function removeSelectedOption(index: number) {
  const options = normalizedFieldOptions(selectedField.value)
  if (options.length <= 1) {
    ElMessage.warning('至少保留一个选项')
    return
  }
  options.splice(index, 1)
  setSelectedFieldOptions(options)
}

async function bulkEditSelectedOptions() {
  if (!selectedField.value) return
  try {
    const result = await ElMessageBox.prompt('每行一个选项，确认后会按当前顺序更新。', '批量编辑选项', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValue: normalizedFieldOptions(selectedField.value).join('\n'),
      inputValidator: (value) => String(value || '').split(/\r?\n/).some((item) => item.trim()) || '至少填写一个选项',
    })
    const options = String(result.value || '')
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean)
    setSelectedFieldOptions(options)
  } catch (error: any) {
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel' || error?.action === 'close') return
    ElMessage.error('批量编辑选项失败，请稍后重试')
  }
}

function showOptionRelationHint() {
  ElMessage.info('选项关联属于增强能力，当前先保留入口，保存时不会生成不可执行规则')
}

function showValidationHint() {
  ElMessage.info('填写校验入口已保留，后续可在这里继续接入校验条件配置')
}

function showDesignerHelp() {
  ElMessageBox.alert(
    '在模板设置中维护名称、说明和可见范围；在表单设计中添加控件并配置必填、选项和默认值；在流程设置中发布审批节点。保存后移动端会读取同一套模板配置。',
    '表单设计帮助',
    { confirmButtonText: '知道了' },
  )
}

function showDesignerPreviewOnlyHint(feature: string) {
  ElMessage.info(`${feature}在移动端发起申请时执行，当前设计器中仅展示控件效果`)
}

function showOptionImageHint() {
  ElMessage.info('选项图片当前不参与移动端渲染，已避免保存不可执行配置')
}

function showEnhancedFeatureHint() {
  ElMessage.info('数据联动和公式编辑当前未启用，发布模板时不会生成不可执行规则')
}

function setSelectedDetailSummaryEnabled(event: Event) {
  const target = event.target as HTMLInputElement | null
  const summary = ensureDetailSummary(selectedField.value)
  if (!summary) return
  summary.enabled = Boolean(target?.checked)
  if (!summary.label) {
    summary.label = defaultDetailSummaryLabel(selectedField.value)
  }
}

function setSelectedDetailSummaryLabel(event: Event) {
  const target = event.target as HTMLInputElement | null
  const summary = ensureDetailSummary(selectedField.value)
  if (!summary) return
  summary.label = target?.value || ''
}

function toggleSelectedDetailSummaryField(code: string) {
  const summary = ensureDetailSummary(selectedField.value)
  if (!summary) return
  const currentCodes = detailSummaryFieldCodes(selectedField.value)
  const numericCodes = numericDetailChildFields(selectedField.value).map((field) => field.code)
  const selected = new Set(currentCodes.length ? currentCodes : numericCodes)
  if (selected.has(code)) {
    selected.delete(code)
  } else {
    selected.add(code)
  }
  summary.field_codes = Array.from(selected).filter((fieldCode) => numericCodes.includes(String(fieldCode)))
}

function fieldOptionsText(field: TemplateField | null | undefined) {
  return normalizedFieldOptions(field).join('\n')
}

function onFieldOptionsInput(event: Event) {
  if (!selectedField.value) return
  const target = event.target as HTMLTextAreaElement | null
  const options = String(target?.value || '')
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
  setSelectedFieldOptions(options)
}

function onSelectedFieldTypeChange() {
  if (!selectedField.value) return
  const control = controlDefinitionForType(selectedField.value.field_type)
  selectedField.value.placeholder = fieldPlaceholder(selectedField.value)
  selectedField.value.is_readonly = Boolean(control?.readonly_by_default)
  selectedField.value.is_business_calculation = Boolean(control?.business_calculation)
  if (fieldSupportsOptions(selectedField.value)) {
    selectedField.value.options_json = optionPayload(control?.default_options || normalizedFieldOptions(selectedField.value)) || { options: ['选项1', '选项2'] }
  } else if (selectedField.value.field_type === 'detail') {
    selectedField.value.options_json = defaultDetailOptions(selectedField.value)
  } else {
    selectedField.value.options_json = undefined
  }
  if (control?.supports_print === false) {
    selectedField.value.printable = false
  }
}

function fieldSupportsLocationRange(field: TemplateField | null | undefined) {
  return field?.field_type === 'location'
}

function fieldSupportsDurationRules(field: TemplateField | null | undefined) {
  return field?.field_type === 'duration'
}

function durationScaleHint(field: TemplateField | null | undefined) {
  return controlOption(field, 'time_scale', 'day') === 'hour'
    ? '时长按小时展示和计算'
    : '时长按天展示和计算'
}

function staticTextContent(field: TemplateField | null | undefined) {
  return field?.placeholder || ''
}

function onStaticTextInput(event: Event) {
  if (!selectedField.value) return
  const target = event.target as HTMLTextAreaElement | null
  selectedField.value.placeholder = target?.value || ''
}

function insertStaticTextLink() {
  if (!selectedField.value) return
  const text = staticTextContent(selectedField.value)
  selectedField.value.placeholder = `${text}${text ? '\n' : ''}https://`
}

function toggleRelatedApprovalCode(code: string) {
  const options = ensureSelectedFieldOptions()
  if (!options) return
  const selected = new Set(selectedRelatedApprovalCodes.value)
  if (selected.has(code)) {
    selected.delete(code)
  } else {
    selected.add(code)
  }
  options.related_template_codes = Array.from(selected)
}

function attendanceComponentHint(field: TemplateField | null | undefined) {
  if (!field) return ''
  const component = fieldAttendanceComponent(field)
  if (component === 'overtime' && field.field_type === 'duration') return '加班时长单位优先沿用打卡规则-加班规则中的配置'
  if (component === 'shift') return '调班组件通常与上下班打卡规则中的排班配置配合使用'
  if (component === 'punch_correction') return '补卡组件通常与上下班打卡规则中的补卡配置配合使用'
  if (field.field_type === 'duration') return '可结合开始时间和结束时间自动计算时长'
  if (field.field_type === 'location') return '可在移动端采集定位信息'
  return ''
}

function fieldShowsLeaveTypeTable(field: TemplateField | null | undefined) {
  return Boolean(field && field.field_type === 'select' && field.code === 'leave_type' && (
    currentTemplate.name === '请假' || fieldAttendanceComponent(field) === 'leave'
  ))
}

function normalizeRuleSettings() {
  ruleSettings.visibleDepartmentIds = []
  ruleSettings.includeSubDepartments = false
  ruleSettings.submitPermissionType = 'all'
  ruleSettings.submitRoles = []
  ruleSettings.submitMemberIds = []
  ruleSettings.viewPermissionType = 'all'
  ruleSettings.templateAdminType = 'all'
  ruleSettings.templateAdminIds = []
  ruleSettings.fixedApproverLocked = true
  ruleSettings.fixedCcLocked = true
  ruleSettings.fixedHandlerLocked = true
  ruleSettings.approvalCommentRequired = false
  ruleSettings.handlerCommentRequired = false
}

let editorUidSequence = 0
function uniqueUid(prefix: string) {
  editorUidSequence += 1
  return `${prefix}_${Date.now()}_${editorUidSequence}_${Math.random().toString(16).slice(2, 8)}`
}

function isConditionBranchNode(node: FlowNodeEditor | null | undefined) {
  return node?.node_type === 'condition_branch'
}

function isParallelBranchNode(node: FlowNodeEditor | null | undefined) {
  return node?.node_type === 'parallel_branch'
}

function isSplitFlowNode(node: FlowNodeEditor | null | undefined) {
  return isConditionBranchNode(node) || isParallelBranchNode(node)
}

function createFlowNode(type: FlowNodeType = 'approval'): FlowNodeEditor {
  const isNotifyNode = type === 'notify'
  const isSplitNode = type === 'condition_branch' || type === 'parallel_branch'
  return {
    uid: uniqueUid('node'),
    node_type: type,
    assignee_source: isNotifyNode ? 'applicant_select' : 'direct_manager',
    approver_id: null,
    approver_ids: [],
    role: 'manager',
    approval_mode: 'or_sign',
    allow_applicant_select: isNotifyNode,
    include_applicant_self: false,
    applicant_select_mode: 'multiple',
    applicant_select_scope: 'company',
    applicant_select_member_ids: [],
    applicant_select_roles: [],
    notify_sources: isNotifyNode ? ['applicant_select'] : [],
    related_field_code: '',
    related_member_field_code: '',
    related_department_field_code: '',
    manager_level: 1,
    fallback_to_upper_manager: true,
    applicant_self_if_head: false,
    multi_level_end_type: 'level',
    multi_level_role: 'manager',
    multi_level_member_ids: [],
    multi_level_limit_enabled: false,
    multi_level_limit_level: 1,
    multi_level_level: 'highest',
    empty_action: 'transfer_to_admin',
    empty_member_id: null,
    vote_pass_count: 1,
    auto_action: type === 'auto' ? 'approve' : 'approve',
    branches: type === 'condition_branch'
      ? [
        createFlowBranch('条件1', false),
        createFlowBranch('默认条件', true),
      ]
      : type === 'parallel_branch'
        ? [createParallelBranch('分支1'), createParallelBranch('分支2')]
        : undefined,
    active_branch_uid: isSplitNode ? '' : undefined,
  }
}

function createFlowBranch(label: string, isDefault = false): FlowBranchEditor {
  const conditionGroups = isDefault ? [] : [createFlowConditionGroup()]
  return {
    uid: uniqueUid('branch'),
    label,
    is_default_branch: isDefault,
    condition_combinator: 'and',
    conditions: conditionGroups[0]?.conditions || [],
    condition_group_combinator: 'or',
    condition_groups: conditionGroups,
    nodes: [],
  }
}

function createParallelBranch(label: string): FlowBranchEditor {
  return {
    uid: uniqueUid('branch'),
    label,
    is_default_branch: false,
    condition_combinator: 'and',
    conditions: [],
    condition_group_combinator: 'or',
    condition_groups: [],
    nodes: [],
  }
}

function createFlowConditionGroup(label = '条件组'): FlowConditionGroupEditor {
  return {
    uid: uniqueUid('condition_group'),
    label,
    condition_combinator: 'and',
    conditions: [createFlowCondition()],
  }
}

function createFlowCondition(field?: string): FlowConditionEditor {
  const conditionField = normalizeConditionField(field || conditionFieldOptions.value[0]?.value || 'applicant.scope')
  return {
    uid: uniqueUid('condition'),
    field: conditionField,
    operator: defaultOperatorForConditionField(conditionField),
    value: defaultValueForConditionField(conditionField),
  }
}

function defaultFlowNodes(): FlowNodeEditor[] {
  return [createFlowNode('approval'), createFlowNode('notify')]
}

function nodeTypeLabel(type: FlowNodeType) {
  if (type === 'approval') return '审批人'
  if (type === 'notify') return '抄送人'
  if (type === 'handler') return '办理人'
  if (type === 'condition_branch') return '条件分支'
  if (type === 'parallel_branch') return '并行分支'
  return '自动处理'
}

function nodeCardClass(type: FlowNodeType) {
  if (type === 'approval') return 'flow-approval'
  if (type === 'notify') return 'flow-notify'
  if (type === 'handler') return 'flow-handler'
  if (type === 'condition_branch') return 'flow-condition'
  if (type === 'parallel_branch') return 'flow-parallel'
  return 'flow-auto'
}

function memberNameById(id: number | null) {
  if (!id) return '未指定'
  const member = memberOptions.value.find((item) => item.id === id)
  return member ? member.name : `员工#${id}`
}

function normalizeMemberIds(ids: unknown): number[] {
  const raw = Array.isArray(ids) ? ids : ids === null || ids === undefined ? [] : [ids]
  const seen = new Set<number>()
  const normalized: number[] = []
  for (const item of raw) {
    const id = Number(item)
    if (!Number.isFinite(id) || id <= 0 || seen.has(id)) continue
    seen.add(id)
    normalized.push(id)
  }
  return normalized
}

function roleLabel(value: string) {
  return approvalRoleOptions.find((role) => role.value === value)?.label || value || '未指定'
}

function normalizeRoleValues(values: unknown): string[] {
  const raw = Array.isArray(values) ? values : values === null || values === undefined || values === '' ? [] : [values]
  const supported = new Set(approvalRoleOptions.map((role) => role.value))
  const seen = new Set<string>()
  const normalized: string[] = []
  for (const item of raw) {
    const value = String(item || '').trim()
    if (!value || !supported.has(value) || seen.has(value)) continue
    seen.add(value)
    normalized.push(value)
  }
  return normalized
}

function applicantSelectScopeValue(value: unknown): ApplicantSelectScope {
  if (value === 'selected_members' || value === 'role') return value
  return 'company'
}

function roleLabels(values: unknown) {
  const roles = normalizeRoleValues(values)
  return roles.length ? roles.map(roleLabel).join('、') : '未指定'
}

function flowNodeMemberIds(node: FlowNodeEditor) {
  const ids = normalizeMemberIds(node.approver_ids)
  if (ids.length) return ids
  return normalizeMemberIds(node.approver_id)
}

function memberNamesByIds(ids: number[]) {
  if (!ids.length) return '未指定'
  return ids.map((id) => memberNameById(id)).join('、')
}

function memberNamesForFlowNode(node: FlowNodeEditor) {
  return memberNamesByIds(flowNodeMemberIds(node))
}

function applicantSelectMemberNames(node: FlowNodeEditor) {
  return memberNamesByIds(normalizeMemberIds(node.applicant_select_member_ids))
}

function applicantSelectRoleNames(node: FlowNodeEditor) {
  return roleLabels(node.applicant_select_roles)
}

function multiLevelMemberNames(node: FlowNodeEditor) {
  const ids = normalizeMemberIds(node.multi_level_member_ids)
  return ids.length ? memberNamesByIds(ids) : ''
}

function isApprovalLikeNode(node: FlowNodeEditor | null | undefined) {
  if (!node) return false
  return node.node_type === 'approval' || node.node_type === 'auto' || node.node_type === 'handler'
}

function nodeDialogTitle(node: FlowNodeEditor) {
  if (node.node_type === 'notify') return '抄送人设置'
  if (node.node_type === 'auto') return '自动处理'
  return `${nodeTypeLabel(node.node_type)}设置`
}

function approvalExecutionType(node: FlowNodeEditor): ApprovalExecutionType {
  if (node.node_type === 'auto' && node.auto_action === 'reject') return 'auto_reject'
  if (node.node_type === 'auto') return 'auto_approve'
  return 'manual'
}

function setApprovalExecutionType(node: FlowNodeEditor, type: ApprovalExecutionType) {
  if (type === 'manual') {
    node.node_type = 'approval'
    node.auto_action = 'approve'
  } else {
    node.node_type = 'auto'
    node.auto_action = type === 'auto_reject' ? 'reject' : 'approve'
    node.assignee_source = 'direct_manager'
    node.approver_id = null
    node.approver_ids = []
  }
  markRulesDirty()
}

function isSourceSelected(node: FlowNodeEditor, source: AssigneeSource) {
  return normalizeAssigneeSource(node.assignee_source) === source
}

function sourceUnavailableReason(source: AssigneeSource) {
  if (source === 'related_member_field' && !memberFieldOptions.value.length) return '表单中还没有成员控件，暂不可选'
  if (source === 'form_department_head' && !departmentFieldOptions.value.length) return '表单中还没有部门控件，暂不可选'
  return ''
}

function selectAssigneeSource(node: FlowNodeEditor, source: AssigneeSource) {
  const reason = sourceUnavailableReason(source)
  if (reason) {
    ElMessage.warning(reason)
    return
  }
  const previousSource = normalizeAssigneeSource(node.assignee_source)
  node.assignee_source = source
  if (source !== 'specific_user') {
    node.approver_id = null
    node.approver_ids = []
  }
  if (source !== 'role') {
    node.role = 'manager'
  }
  if (source === 'multi_level_manager') {
    node.approval_mode = 'sequential'
    if (previousSource !== 'multi_level_manager') {
      node.multi_level_end_type = 'level'
      node.multi_level_limit_enabled = false
      node.multi_level_level = 'highest'
    }
    node.multi_level_role = node.multi_level_role || 'manager'
    node.multi_level_member_ids = normalizeMemberIds(node.multi_level_member_ids)
  }
  if (source === 'applicant_select') {
    node.applicant_select_mode = node.applicant_select_mode || 'multiple'
    node.applicant_select_scope = node.applicant_select_scope || 'company'
    node.applicant_select_member_ids = normalizeMemberIds(node.applicant_select_member_ids)
    node.applicant_select_roles = normalizeRoleValues(node.applicant_select_roles)
  }
  if (source === 'related_member_field' && !node.related_member_field_code) {
    node.related_member_field_code = memberFieldOptions.value[0]?.code || ''
    node.related_field_code = node.related_member_field_code
  }
  if (source === 'form_department_head' && !node.related_department_field_code) {
    node.related_department_field_code = departmentFieldOptions.value[0]?.code || ''
    node.related_field_code = node.related_department_field_code
  }
  markRulesDirty()
}

function notifySources(node: FlowNodeEditor) {
  if (node.notify_sources?.length) return node.notify_sources.map(normalizeAssigneeSource)
  const fallback = normalizeAssigneeSource(node.assignee_source)
  return fallback ? [fallback] : []
}

function isNotifySourceSelected(node: FlowNodeEditor, source: AssigneeSource) {
  return notifySources(node).includes(source)
}

function toggleNotifySource(node: FlowNodeEditor, source: AssigneeSource) {
  const reason = sourceUnavailableReason(source)
  if (reason) {
    ElMessage.warning(reason)
    return
  }
  const selected = new Set(notifySources(node))
  if (selected.has(source)) {
    if (selected.size <= 1) {
      ElMessage.warning('至少保留一个抄送人来源')
      return
    }
    selected.delete(source)
  } else {
    selected.add(source)
  }
  node.notify_sources = Array.from(selected)
  node.assignee_source = node.notify_sources[0] || 'specific_user'
  node.allow_applicant_select = selected.has('applicant_select')
  node.include_applicant_self = selected.has('applicant_self')
  if (source === 'specific_user' && !selected.has(source)) {
    node.approver_id = null
    node.approver_ids = []
  }
  if (selected.has('related_member_field') && !node.related_member_field_code) {
    node.related_member_field_code = memberFieldOptions.value[0]?.code || ''
  }
  if (selected.has('form_department_head') && !node.related_department_field_code) {
    node.related_department_field_code = departmentFieldOptions.value[0]?.code || ''
  }
  markRulesDirty()
}

function showDisabledNodeOption(reason: string) {
  ElMessage.warning(reason || '该选项暂不可选')
}

function setRelatedMemberField(node: FlowNodeEditor, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  node.related_member_field_code = value
  if (node.assignee_source === 'related_member_field') node.related_field_code = value
  markRulesDirty()
}

function setRelatedDepartmentField(node: FlowNodeEditor, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  node.related_department_field_code = value
  if (node.assignee_source === 'form_department_head') node.related_field_code = value
  markRulesDirty()
}

function setNodeEmptyAction(node: FlowNodeEditor, action: string) {
  node.empty_action = ['auto_approve', 'auto_reject', 'transfer_to_admin', 'transfer_to_specific'].includes(action)
    ? action as FlowNodeEditor['empty_action']
    : ''
  if (action !== 'transfer_to_specific') {
    node.empty_member_id = null
  }
  markRulesDirty()
}

function onApplicantSelectModeChange(node: FlowNodeEditor) {
  if (node.applicant_select_mode === 'single' && node.approval_mode === 'counter_sign') {
    node.approval_mode = 'or_sign'
  }
  markRulesDirty()
}

function onApplicantSelectScopeChange(node: FlowNodeEditor) {
  node.applicant_select_member_ids = normalizeMemberIds(node.applicant_select_member_ids)
  node.applicant_select_roles = normalizeRoleValues(node.applicant_select_roles)
  markRulesDirty()
}

function setMultiLevelEndType(node: FlowNodeEditor, type: FlowNodeEditor['multi_level_end_type']) {
  node.multi_level_end_type = type
  node.approval_mode = 'sequential'
  if (type === 'role') {
    node.multi_level_role = node.multi_level_role || 'manager'
    node.multi_level_limit_enabled = node.multi_level_limit_enabled !== false
    node.multi_level_limit_level = node.multi_level_limit_level || 1
  } else if (type === 'member') {
    node.multi_level_member_ids = normalizeMemberIds(node.multi_level_member_ids)
    node.multi_level_limit_enabled = node.multi_level_limit_enabled !== false
    node.multi_level_limit_level = node.multi_level_limit_level || 1
  } else {
    node.multi_level_level = node.multi_level_level || 'highest'
  }
  markRulesDirty()
}

async function selectMultiLevelMemberEndType(node: FlowNodeEditor) {
  setMultiLevelEndType(node, 'member')
  await openMemberPicker(node, 'multi_level_member')
}

function showApprovalModeOptions(node: FlowNodeEditor) {
  if (node.node_type !== 'approval' && node.node_type !== 'handler') return false
  if (node.assignee_source === 'applicant_self' || node.assignee_source === 'direct_manager' || node.assignee_source === 'multi_level_manager') return false
  if (node.assignee_source === 'applicant_select') return node.applicant_select_mode === 'multiple'
  if (node.assignee_source === 'specific_user') return flowNodeMemberIds(node).length > 1
  return true
}

function showEmptyApproverOptions(node: FlowNodeEditor) {
  return ['direct_manager', 'department_head', 'multi_level_manager', 'related_member_field', 'form_department_head'].includes(node.assignee_source)
}

function hasMissingSpecificMember(node: FlowNodeEditor) {
  if (node.node_type === 'auto') return false
  if (node.node_type === 'notify') {
    return isNotifySourceSelected(node, 'specific_user') && flowNodeMemberIds(node).length === 0
  }
  if (node.assignee_source !== 'specific_user') return false
  return flowNodeMemberIds(node).length === 0
}

function collectExecutableFlowNodes(nodes: FlowNodeEditor[]): FlowNodeEditor[] {
  const result: FlowNodeEditor[] = []
  for (const node of nodes) {
    if (isSplitFlowNode(node)) {
      for (const branch of node.branches || []) {
        result.push(...collectExecutableFlowNodes(branch.nodes || []))
      }
    } else {
      result.push(node)
    }
  }
  return result
}

function collectBlockingFlowNodes(nodes: FlowNodeEditor[]): FlowNodeEditor[] {
  return collectExecutableFlowNodes(nodes).filter((node) => (
    node.node_type === 'approval' || node.node_type === 'handler' || node.node_type === 'auto'
  ))
}

function ensureDefaultManualNodeTypes(nodes: FlowNodeEditor[]) {
  const executableNodes = collectExecutableFlowNodes(nodes)
  if (!executableNodes.some((node) => node.node_type === 'approval')) {
    nodes.push(createFlowNode('approval'))
  }
  if (!executableNodes.some((node) => node.node_type === 'notify')) {
    nodes.push(createFlowNode('notify'))
  }
  return nodes
}

function findFlowNodeByUid(nodes: FlowNodeEditor[], uid: string): FlowNodeEditor | null {
  if (!uid) return null
  for (const node of nodes) {
    if (node.uid === uid) return node
    if (!isSplitFlowNode(node)) continue
    for (const branch of node.branches || []) {
      const found = findFlowNodeByUid(branch.nodes || [], uid)
      if (found) return found
    }
  }
  return null
}

function findFlowBranchByUid(nodes: FlowNodeEditor[], branchUid: string): FlowBranchEditor | null {
  for (const node of nodes) {
    if (isSplitFlowNode(node)) {
      const direct = (node.branches || []).find((branch) => branch.uid === branchUid)
      if (direct) return direct
      for (const branch of node.branches || []) {
        const found = findFlowBranchByUid(branch.nodes || [], branchUid)
        if (found) return found
      }
    }
  }
  return null
}

function collapseDefaultOnlyConditionBranches(nodes: FlowNodeEditor[]): boolean {
  let changed = false
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index]
    if (isSplitFlowNode(node)) {
      for (const branch of node.branches || []) {
        if (collapseDefaultOnlyConditionBranches(branch.nodes || [])) {
          changed = true
        }
      }
    }
    if (!isConditionBranchNode(node)) continue
    const branches = node.branches || []
    const defaultBranch = branches.find((branch) => branch.is_default_branch)
    const conditionalBranchCount = branches.filter((branch) => !branch.is_default_branch).length
    if (defaultBranch && conditionalBranchCount === 0) {
      nodes.splice(index, 1, ...(defaultBranch.nodes || []))
      changed = true
    }
  }
  return changed
}

function syncFlowSelectionAfterStructureChange() {
  if (!selectedFlowNodeUid.value) return
  if (findFlowNodeByUid(flowNodes.value, selectedFlowNodeUid.value)) return
  selectedFlowNodeUid.value = ''
  flowConfigDrawerVisible.value = false
}

function normalizeFlowTopologyAfterMutation() {
  if (collapseDefaultOnlyConditionBranches(flowNodes.value)) {
    syncFlowSelectionAfterStructureChange()
  }
}

function removeFlowNodeByUid(nodes: FlowNodeEditor[], uid: string): boolean {
  const index = nodes.findIndex((node) => node.uid === uid)
  if (index >= 0) {
    nodes.splice(index, 1)
    return true
  }
  for (const node of nodes) {
    if (!isSplitFlowNode(node)) continue
    for (const branch of node.branches || []) {
      if (removeFlowNodeByUid(branch.nodes || [], uid)) {
        return true
      }
    }
  }
  return false
}

function findUnsupportedTemplateField(fields: TemplateField[]): TemplateField | null {
  for (const field of fields) {
    if (!supportedTemplateFieldTypes.has(normalizeTemplateFieldType(field.field_type, field.code))) {
      return field
    }
    const childFields = field.field_type === 'detail' && Array.isArray(field.options_json?.child_fields)
      ? field.options_json.child_fields as TemplateField[]
      : []
    const invalidChild = childFields.length ? findUnsupportedTemplateField(childFields) : null
    if (invalidChild) {
      return invalidChild
    }
  }
  return null
}

function conditionHasValue(condition: FlowConditionEditor) {
  if (condition.operator === 'empty' || condition.operator === 'not_empty') return true
  if (condition.operator === 'between') {
    const values = Array.isArray(condition.value) ? condition.value : []
    return values.length >= 2 && values[0] !== '' && values[0] !== null && values[0] !== undefined && values[1] !== '' && values[1] !== null && values[1] !== undefined
  }
  if (conditionFieldKind(condition.field) === 'applicant' || conditionFieldKind(condition.field) === 'selection') {
    return conditionValueArray(condition).length > 0
  }
  return condition.value !== '' && condition.value !== null && condition.value !== undefined
}

function validateConditionBranches(nodes: FlowNodeEditor[]): boolean {
  for (const node of nodes) {
    if (isParallelBranchNode(node)) {
      ensureParallelBranches(node)
      const branches = node.branches || []
      if (branches.length < 2) {
        ElMessage.error('每个并行分支至少需要保留两条分支')
        return false
      }
      for (const branch of branches) {
        if (!collectExecutableFlowNodes(branch.nodes || []).length) {
          ElMessage.error(`并行分支“${branch.label || '未命名分支'}”需至少添加一个节点`)
          return false
        }
        if (containsParallelBranchNode(branch.nodes || [])) {
          ElMessage.error('并行分支内暂不支持再次嵌套并行分支')
          return false
        }
        if (!validateConditionBranches(branch.nodes || [])) return false
      }
      continue
    }
    if (!isConditionBranchNode(node)) continue
    ensureConditionBranches(node)
    const branches = node.branches || []
    if (branches.filter((branch) => branch.is_default_branch).length !== 1) {
      ElMessage.error('每个条件分支必须保留一个默认分支')
      return false
    }
    for (const branch of branches) {
      if (branch.is_default_branch) {
        if (!validateConditionBranches(branch.nodes)) return false
        continue
      }
      ensureBranchConditionGroups(branch)
      const groups = branch.condition_groups || []
      if (!groups.length) {
        ElMessage.error(`分支“${branch.label || '未命名分支'}”需至少配置一个条件组`)
        return false
      }
      for (const group of groups) {
        if (!group.conditions?.length) {
          ElMessage.error(`分支“${branch.label || '未命名分支'}”存在空条件组`)
          return false
        }
        for (const condition of group.conditions) {
          if (!conditionHasValue(condition)) {
            ElMessage.error(`分支“${branch.label || '未命名分支'}”的“${conditionFieldLabel(condition.field)}”条件未填写完整`)
            return false
          }
        }
      }
      if (!validateConditionBranches(branch.nodes)) return false
    }
  }
  return true
}

function containsParallelBranchNode(nodes: FlowNodeEditor[]): boolean {
  for (const node of nodes) {
    if (isParallelBranchNode(node)) return true
    if (isSplitFlowNode(node)) {
      for (const branch of node.branches || []) {
        if (containsParallelBranchNode(branch.nodes || [])) return true
      }
    }
  }
  return false
}

function ensureConditionBranches(node: FlowNodeEditor) {
  if (!isConditionBranchNode(node)) return
  if (!node.branches?.length) {
    node.branches = [createFlowBranch('条件1', false), createFlowBranch('默认条件', true)]
  }
  if (!node.branches.some((branch) => branch.is_default_branch)) {
    node.branches.push(createFlowBranch('默认条件', true))
  }
  node.branches.forEach((branch, index) => {
    if (branch.is_default_branch && (!branch.label || branch.label === '默认分支')) {
      branch.label = '默认条件'
    }
    if (!branch.is_default_branch && !branch.label) {
      branch.label = `条件${index + 1}`
    }
  })
  const firstDefaultIndex = node.branches.findIndex((branch) => branch.is_default_branch)
  if (firstDefaultIndex >= 0) {
    node.branches.forEach((branch, index) => {
      const shouldBeDefault = index === firstDefaultIndex
      if (branch.is_default_branch !== shouldBeDefault) {
        branch.is_default_branch = shouldBeDefault
      }
    })
  }
  if (!node.active_branch_uid || !node.branches.some((branch) => branch.uid === node.active_branch_uid)) {
    node.active_branch_uid = node.branches[0]?.uid
  }
  node.branches.forEach((branch) => ensureBranchConditionGroups(branch))
}

function ensureParallelBranches(node: FlowNodeEditor) {
  if (!isParallelBranchNode(node)) return
  if (!node.branches?.length) {
    node.branches = [createParallelBranch('分支1'), createParallelBranch('分支2')]
  }
  while (node.branches.length < 2) {
    node.branches.push(createParallelBranch(`分支${node.branches.length + 1}`))
  }
  node.branches.forEach((branch, index) => {
    branch.is_default_branch = false
    branch.conditions = []
    branch.condition_groups = []
    branch.condition_combinator = 'and'
    branch.condition_group_combinator = 'or'
    if (!branch.label) branch.label = `分支${index + 1}`
  })
  if (!node.active_branch_uid || !node.branches.some((branch) => branch.uid === node.active_branch_uid)) {
    node.active_branch_uid = node.branches[0]?.uid
  }
}

function createConditionBranchNodeWithPrimaryNodes(primaryNodes: FlowNodeEditor[] = []) {
  const node = createFlowNode('condition_branch')
  ensureConditionBranches(node)
  const primaryBranch = node.branches?.find((branch) => !branch.is_default_branch)
  if (primaryBranch) {
    primaryBranch.nodes = primaryNodes
    node.active_branch_uid = primaryBranch.uid
  }
  return node
}

function ensureBranchConditionGroups(branch: FlowBranchEditor) {
  if (branch.is_default_branch) {
    if (branch.condition_groups?.length) branch.condition_groups = []
    if (branch.conditions?.length) branch.conditions = []
    if (branch.condition_group_combinator !== 'or') branch.condition_group_combinator = 'or'
    return
  }
  if (!branch.condition_group_combinator) {
    branch.condition_group_combinator = 'or'
  }
  if (!branch.condition_groups?.length) {
    branch.condition_groups = [{
      uid: uniqueUid('condition_group'),
      label: '条件组',
      condition_combinator: branch.condition_combinator || 'and',
      conditions: branch.conditions?.length ? branch.conditions : [createFlowCondition()],
    }]
  }
  branch.condition_groups.forEach((group) => {
    if (!group.uid) group.uid = uniqueUid('condition_group')
    if (!group.label) group.label = '条件组'
    if (group.condition_combinator !== 'or') group.condition_combinator = 'and'
    if (!group.conditions?.length) group.conditions = [createFlowCondition()]
    group.conditions.forEach((condition) => normalizeConditionEditor(condition))
  })
  const primaryConditions = branch.condition_groups[0]?.conditions || []
  if (branch.conditions !== primaryConditions) branch.conditions = primaryConditions
  const primaryCombinator = branch.condition_groups[0]?.condition_combinator || 'and'
  if (branch.condition_combinator !== primaryCombinator) branch.condition_combinator = primaryCombinator
}

function setActiveBranch(branchUid: string) {
  if (!selectedFlowNode.value) return
  selectedFlowNode.value.active_branch_uid = branchUid
}

function addConditionBranchToNode(node: FlowNodeEditor) {
  if (isParallelBranchNode(node)) {
    ensureParallelBranches(node)
    const branch = createParallelBranch(`分支${(node.branches || []).length + 1}`)
    node.branches?.push(branch)
    node.active_branch_uid = branch.uid
    markRulesDirty()
    return branch
  }
  if (!isConditionBranchNode(node)) return null
  ensureConditionBranches(node)
  const existingConditionalCount = (node.branches || []).filter((branch) => !branch.is_default_branch).length
  const branch = createFlowBranch(`条件${existingConditionalCount + 1}`, false)
  const defaultIndex = node.branches?.findIndex((item) => item.is_default_branch) ?? -1
  if (defaultIndex >= 0) {
    node.branches?.splice(defaultIndex, 0, branch)
  } else {
    node.branches?.push(branch)
  }
  node.active_branch_uid = branch.uid
  markRulesDirty()
  return branch
}

function addConditionBranch() {
  if (!selectedFlowNode.value) return
  addConditionBranchToNode(selectedFlowNode.value)
}

function addConditionBranchFromCanvas(payload: { nodeUid: string }) {
  const node = findFlowNodeByUid(flowNodes.value, payload.nodeUid)
  if (!node || !isSplitFlowNode(node)) return
  const branch = addConditionBranchToNode(node)
  if (!branch) return
  selectedFlowNodeUid.value = node.uid
  flowConfigDrawerVisible.value = false
}

function cloneFlowConditionValue(value: FlowConditionValue): FlowConditionValue {
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === 'object' && item) return { ...item }
      return item
    })
  }
  if (typeof value === 'object' && value) return { ...value }
  return value
}

function cloneFlowConditionForCopy(condition: FlowConditionEditor): FlowConditionEditor {
  const copied: FlowConditionEditor = {
    uid: uniqueUid('condition'),
    field: normalizeConditionField(condition.field),
    operator: condition.operator,
    value: cloneFlowConditionValue(condition.value),
  }
  normalizeConditionEditor(copied)
  return copied
}

function cloneConditionGroupForCopy(group: FlowConditionGroupEditor): FlowConditionGroupEditor {
  return {
    uid: uniqueUid('condition_group'),
    label: group.label || '条件组',
    condition_combinator: group.condition_combinator || 'and',
    conditions: group.conditions?.length ? group.conditions.map(cloneFlowConditionForCopy) : [createFlowCondition()],
  }
}

function cloneFlowNodeForBranchCopy(node: FlowNodeEditor): FlowNodeEditor {
  const copied: FlowNodeEditor = {
    uid: uniqueUid('node'),
    node_type: node.node_type,
    assignee_source: normalizeAssigneeSource(node.assignee_source),
    approver_id: node.approver_id,
    approver_ids: [...normalizeMemberIds(node.approver_ids?.length ? node.approver_ids : node.approver_id)],
    role: node.role,
    approval_mode: node.approval_mode,
    allow_applicant_select: node.allow_applicant_select,
    include_applicant_self: node.include_applicant_self,
    applicant_select_mode: node.applicant_select_mode || 'multiple',
    applicant_select_scope: node.applicant_select_scope || 'company',
    applicant_select_member_ids: [...normalizeMemberIds(node.applicant_select_member_ids)],
    applicant_select_roles: normalizeRoleValues(node.applicant_select_roles),
    notify_sources: [...(node.notify_sources || [])],
    related_field_code: node.related_field_code || '',
    related_member_field_code: node.related_member_field_code || '',
    related_department_field_code: node.related_department_field_code || '',
    manager_level: node.manager_level || 1,
    fallback_to_upper_manager: node.fallback_to_upper_manager !== false,
    applicant_self_if_head: Boolean(node.applicant_self_if_head),
    multi_level_end_type: node.multi_level_end_type || 'level',
    multi_level_role: node.multi_level_role || node.role || 'manager',
    multi_level_member_ids: [...normalizeMemberIds(node.multi_level_member_ids)],
    multi_level_limit_enabled: Boolean(node.multi_level_limit_enabled),
    multi_level_limit_level: node.multi_level_limit_level || 1,
    multi_level_level: node.multi_level_level || 'highest',
    empty_action: node.empty_action || '',
    empty_member_id: node.empty_member_id || null,
    vote_pass_count: Number(node.vote_pass_count || 1),
    auto_action: node.auto_action || 'approve',
  }
  if (isConditionBranchNode(node)) {
    copied.branches = (node.branches || []).map((branch) => cloneFlowBranchForCopy(branch, branch.label, branch.is_default_branch))
    copied.active_branch_uid = copied.branches.find((branch) => !branch.is_default_branch)?.uid || copied.branches[0]?.uid
    ensureConditionBranches(copied)
  } else if (isParallelBranchNode(node)) {
    copied.branches = (node.branches || []).map((branch, index) => cloneParallelBranchForCopy(branch, branch.label || `分支${index + 1}`))
    copied.active_branch_uid = copied.branches[0]?.uid
    ensureParallelBranches(copied)
  }
  return copied
}

function cloneFlowBranchForCopy(branch: FlowBranchEditor, label: string, isDefault = false): FlowBranchEditor {
  const conditionGroups = isDefault ? [] : (
    branch.condition_groups?.length
      ? branch.condition_groups.map(cloneConditionGroupForCopy)
      : [createFlowConditionGroup()]
  )
  const copied: FlowBranchEditor = {
    uid: uniqueUid('branch'),
    label,
    is_default_branch: isDefault,
    condition_combinator: branch.condition_combinator || 'and',
    conditions: conditionGroups[0]?.conditions || [],
    condition_group_combinator: branch.condition_group_combinator || 'or',
    condition_groups: conditionGroups,
    nodes: Array.isArray(branch.nodes) ? branch.nodes.map(cloneFlowNodeForBranchCopy) : [],
  }
  ensureBranchConditionGroups(copied)
  return copied
}

function cloneParallelBranchForCopy(branch: FlowBranchEditor, label: string): FlowBranchEditor {
  return {
    uid: uniqueUid('branch'),
    label,
    is_default_branch: false,
    condition_combinator: 'and',
    conditions: [],
    condition_group_combinator: 'or',
    condition_groups: [],
    nodes: Array.isArray(branch.nodes) ? branch.nodes.map(cloneFlowNodeForBranchCopy) : [],
  }
}

function copyConditionBranch(payload: { nodeUid: string; branchUid: string }) {
  const node = findFlowNodeByUid(flowNodes.value, payload.nodeUid)
  if (!node || node.node_type !== 'condition_branch') return
  ensureConditionBranches(node)
  const branches = node.branches || []
  const currentIndex = branches.findIndex((branch) => branch.uid === payload.branchUid)
  const sourceBranch = branches[currentIndex]
  if (!sourceBranch || sourceBranch.is_default_branch) return
  const sourceLabel = sourceBranch.label || `条件${currentIndex + 1}`
  const copiedBranch = cloneFlowBranchForCopy(sourceBranch, `${sourceLabel}（复制）`, false)
  const defaultIndex = branches.findIndex((branch) => branch.is_default_branch)
  const insertIndex = Math.min(currentIndex + 1, defaultIndex >= 0 ? defaultIndex : branches.length)
  branches.splice(insertIndex, 0, copiedBranch)
  node.active_branch_uid = copiedBranch.uid
  selectedFlowNodeUid.value = node.uid
  flowConfigDrawerVisible.value = true
  markRulesDirty()
  ElMessage.success('已复制条件')
}

function removeConditionBranch(branchUid: string, node: FlowNodeEditor | null = selectedFlowNode.value) {
  if (!node || !isSplitFlowNode(node)) return
  if (isParallelBranchNode(node)) {
    ensureParallelBranches(node)
    if ((node.branches || []).length <= 2) {
      ElMessage.warning('并行分支至少保留两条分支')
      return
    }
    node.branches = (node.branches || []).filter((item) => item.uid !== branchUid)
    if (node.active_branch_uid === branchUid) {
      node.active_branch_uid = node.branches[0]?.uid
    }
    markRulesDirty()
    return
  }
  ensureConditionBranches(node)
  const branch = node.branches?.find((item) => item.uid === branchUid)
  if (branch?.is_default_branch) return
  node.branches = (node.branches || []).filter((item) => item.uid !== branchUid)
  if (node.active_branch_uid === branchUid) {
    node.active_branch_uid = node.branches.find((item) => !item.is_default_branch)?.uid || node.branches[0]?.uid
  }
  normalizeFlowTopologyAfterMutation()
  markRulesDirty()
}

function removeConditionBranchFromCanvas(payload: { nodeUid: string; branchUid: string }) {
  const node = findFlowNodeByUid(flowNodes.value, payload.nodeUid)
  removeConditionBranch(payload.branchUid, node)
}

function setDefaultBranch(branchUid: string) {
  if (!selectedFlowNode.value || selectedFlowNode.value.node_type !== 'condition_branch') return
  ensureConditionBranches(selectedFlowNode.value)
  selectedFlowNode.value.branches = (selectedFlowNode.value.branches || []).map((branch) => ({
    ...branch,
    is_default_branch: branch.uid === branchUid,
    conditions: branch.uid === branchUid ? [] : (branch.conditions.length ? branch.conditions : [createFlowCondition()]),
    condition_groups: branch.uid === branchUid ? [] : (branch.condition_groups?.length ? branch.condition_groups : [createFlowConditionGroup()]),
    condition_group_combinator: 'or',
  }))
  selectedFlowNode.value.branches.forEach((branch) => ensureBranchConditionGroups(branch))
  markRulesDirty()
}

function setBranchPriority(branchUid: string, value: Event) {
  const node = selectedFlowNode.value
  if (!node || node.node_type !== 'condition_branch') return
  ensureConditionBranches(node)
  const branches = node.branches || []
  const currentIndex = branches.findIndex((branch) => branch.uid === branchUid)
  if (currentIndex < 0) return
  const targetIndex = Math.max(0, Math.min(Number((value.target as HTMLSelectElement).value) - 1, branches.length - 1))
  const [branch] = branches.splice(currentIndex, 1)
  branches.splice(targetIndex, 0, branch)
  node.active_branch_uid = branchUid
  markRulesDirty()
}

function selectedConditionFieldValues(group: FlowConditionGroupEditor) {
  return Array.from(new Set((group.conditions || []).map((condition) => normalizeConditionField(condition.field))))
}

function openConditionPicker(group: FlowConditionGroupEditor) {
  conditionPickerTargetGroup.value = group
  tempConditionFieldValues.value = selectedConditionFieldValues(group)
  conditionPickerVisible.value = true
}

function closeConditionPicker() {
  conditionPickerVisible.value = false
  conditionPickerTargetGroup.value = null
  tempConditionFieldValues.value = []
}

function toggleTempConditionField(field: string, checked: boolean) {
  const normalized = normalizeConditionField(field)
  tempConditionFieldValues.value = checked
    ? Array.from(new Set([...tempConditionFieldValues.value, normalized]))
    : tempConditionFieldValues.value.filter((item) => item !== normalized)
}

function confirmConditionPicker() {
  const group = conditionPickerTargetGroup.value
  if (!group) return
  if (!tempConditionFieldValues.value.length) {
    ElMessage.warning('至少选择一个条件')
    return
  }
  const existingByField = new Map(
    (group.conditions || []).map((condition) => [normalizeConditionField(condition.field), condition] as const),
  )
  group.conditions = conditionFieldOptions.value
    .filter((field) => tempConditionFieldValues.value.includes(field.value))
    .map((field) => {
      const existing = existingByField.get(field.value)
      if (existing) {
        existing.field = field.value
        normalizeConditionEditor(existing)
        return existing
      }
      return createFlowCondition(field.value)
    })
  markRulesDirty()
  closeConditionPicker()
}

function removeBranchCondition(group: FlowConditionGroupEditor, conditionUid: string) {
  group.conditions = group.conditions.filter((condition) => condition.uid !== conditionUid)
  if (!group.conditions.length) {
    group.conditions.push(createFlowCondition())
  }
  markRulesDirty()
}

function addConditionGroup(branch: FlowBranchEditor) {
  ensureBranchConditionGroups(branch)
  branch.condition_groups.push(createFlowConditionGroup())
  branch.condition_group_combinator = 'or'
  markRulesDirty()
}

function removeConditionGroup(branch: FlowBranchEditor, groupUid: string) {
  branch.condition_groups = (branch.condition_groups || []).filter((group) => group.uid !== groupUid)
  if (!branch.condition_groups.length) {
    branch.condition_groups.push(createFlowConditionGroup())
  }
  ensureBranchConditionGroups(branch)
  markRulesDirty()
}

function normalizeConditionField(field: unknown) {
  const fieldName = String(field || '').trim()
  return conditionFieldAliases[fieldName] || fieldName
}

function conditionFieldOption(field: string) {
  const normalized = normalizeConditionField(field)
  return conditionFieldOptions.value.find((option) => option.value === normalized) || conditionFieldOptions.value[0]
}

function conditionFieldKind(field: string): ConditionFieldKind {
  return conditionFieldOption(field)?.valueKind || 'text'
}

function conditionFieldLabel(field: string) {
  return conditionFieldOption(field)?.label || field || '字段'
}

function conditionFieldOptionsForGroup(group: FlowConditionGroupEditor, currentCondition?: FlowConditionEditor) {
  const usedFields = new Set(
    (group.conditions || [])
      .filter((condition) => condition.uid !== currentCondition?.uid)
      .map((condition) => normalizeConditionField(condition.field)),
  )
  return conditionFieldOptions.value.filter((option) => !usedFields.has(option.value))
}

function availableConditionFieldsForGroup(group: FlowConditionGroupEditor) {
  return conditionFieldOptionsForGroup(group)
}

function availableConditionCountForGroup(group: FlowConditionGroupEditor) {
  const usedFields = new Set(selectedConditionFieldValues(group))
  return Math.max(0, conditionFieldOptions.value.length - usedFields.size)
}

function defaultOperatorForConditionField(field: string): ConditionOperator {
  const kind = conditionFieldKind(field)
  if (kind === 'number') return 'lte'
  if (kind === 'selection' || kind === 'applicant' || kind === 'member' || kind === 'department' || kind === 'company') return 'in'
  return 'eq'
}

function defaultValueForConditionField(field: string): FlowConditionValue {
  const kind = conditionFieldKind(field)
  if (kind === 'applicant' || kind === 'selection' || kind === 'member') return []
  if (kind === 'company') return companyOptions.value[0]?.id ?? null
  if (kind === 'department') return departmentOptions.value[0]?.id ?? null
  if (kind === 'number') return null
  return ''
}

function normalizeConditionEditor(condition: FlowConditionEditor) {
  if (!condition.uid) condition.uid = uniqueUid('condition')
  condition.field = normalizeConditionField(condition.field)
  if (!condition.field || !conditionFieldOptions.value.some((option) => option.value === condition.field)) {
    condition.field = conditionFieldOptions.value[0]?.value || 'applicant.scope'
  }
  const operators = conditionOperatorOptions(condition)
  if (!operators.some((option) => option.value === condition.operator)) {
    condition.operator = defaultOperatorForConditionField(condition.field)
  }
  if (condition.operator === 'empty' || condition.operator === 'not_empty') {
    condition.value = null
    return
  }
  const kind = conditionFieldKind(condition.field)
  if ((kind === 'applicant' || kind === 'selection' || kind === 'member') && !Array.isArray(condition.value)) {
    condition.value = condition.value === null || condition.value === undefined || condition.value === '' ? [] : [condition.value as any]
  }
  if (condition.operator === 'between') {
    const values = Array.isArray(condition.value) ? condition.value : [condition.value ?? '', '']
    condition.value = [values[0] ?? '', values[1] ?? '']
  }
}

function onBranchConditionFieldChange(condition: FlowConditionEditor) {
  condition.operator = defaultOperatorForConditionField(condition.field)
  condition.value = defaultValueForConditionField(condition.field)
  markRulesDirty()
}

function onBranchConditionOperatorChange(condition: FlowConditionEditor) {
  if (condition.operator === 'empty' || condition.operator === 'not_empty') {
    condition.value = null
  } else if (condition.operator === 'between') {
    const values = Array.isArray(condition.value) ? condition.value : [condition.value ?? '', '']
    condition.value = [values[0] ?? '', values[1] ?? '']
  } else if (conditionFieldKind(condition.field) === 'number' && Array.isArray(condition.value)) {
    condition.value = condition.value[0] as any ?? null
  } else if (condition.value === null || condition.value === undefined) {
    condition.value = defaultValueForConditionField(condition.field)
  }
  markRulesDirty()
}

function conditionOperatorOptions(condition: FlowConditionEditor): Array<{ value: ConditionOperator; label: string }> {
  const kind = conditionFieldKind(condition.field)
  if (kind === 'number') {
    return [
      { value: 'lt', label: '小于' },
      { value: 'lte', label: '小于等于' },
      { value: 'eq', label: '等于' },
      { value: 'gte', label: '大于等于' },
      { value: 'gt', label: '大于' },
      { value: 'between', label: '介于' },
    ]
  }
  if (kind === 'selection' || kind === 'applicant' || kind === 'member' || kind === 'department' || kind === 'company') {
    return [
      { value: 'in', label: '属于' },
      { value: 'not_in', label: '不属于' },
      { value: 'empty', label: '为空' },
      { value: 'not_empty', label: '不为空' },
    ]
  }
  return [
    { value: 'eq', label: '等于' },
    { value: 'neq', label: '不等于' },
    { value: 'contains', label: '包含' },
    { value: 'not_contains', label: '不包含' },
    { value: 'empty', label: '为空' },
    { value: 'not_empty', label: '不为空' },
  ]
}

function conditionValueDisabled(condition: FlowConditionEditor) {
  return condition.operator === 'empty' || condition.operator === 'not_empty'
}

function conditionSelectionOptions(condition: FlowConditionEditor) {
  return conditionFieldOption(condition.field)?.options || []
}

function conditionValueArray(condition: FlowConditionEditor) {
  return Array.isArray(condition.value) ? condition.value : condition.value === null || condition.value === undefined || condition.value === '' ? [] : [condition.value]
}

function conditionSelectionIncludes(condition: FlowConditionEditor, option: string) {
  return conditionValueArray(condition).some((item) => String(item) === String(option))
}

function toggleConditionSelectionValue(condition: FlowConditionEditor, option: string, checked: boolean) {
  const current = conditionValueArray(condition).map((item) => String(item))
  condition.value = checked
    ? Array.from(new Set([...current, option]))
    : current.filter((item) => item !== option)
  markRulesDirty()
}

function conditionBetweenBound(condition: FlowConditionEditor, index: number) {
  const values = Array.isArray(condition.value) ? condition.value : []
  return values[index] ?? ''
}

function setConditionBetweenBound(condition: FlowConditionEditor, index: number, value: string) {
  const values = Array.isArray(condition.value) ? [...condition.value] : ['', '']
  values[index] = value
  condition.value = values as FlowConditionValue
  markRulesDirty()
}

function setConditionValueFromEvent(condition: FlowConditionEditor, value: Event) {
  condition.value = (value.target as HTMLInputElement | HTMLSelectElement).value
  markRulesDirty()
}

function applicantScopeKey(item: ApplicantScopeValue) {
  return `${item.type}:${item.id ?? item.value ?? item.label ?? ''}`
}

function sortApplicantScopeNodes(nodes: ApplicantScopeTreeNode[]) {
  return nodes.sort((a, b) => String(a.label || '').localeCompare(String(b.label || ''), 'zh-CN'))
}

function buildApplicantCompanyScopeTree(): ApplicantScopeTreeNode[] {
  const map = new Map<number, ApplicantScopeTreeNode>()
  for (const company of companyOptions.value) {
    const scope: ApplicantScopeValue = {
      type: 'company',
      id: company.id,
      label: company.short_name || company.name,
    }
    map.set(company.id, {
      key: applicantScopeKey(scope),
      label: scope.label || `公司#${company.id}`,
      scope,
      children: [],
    })
  }
  const roots: ApplicantScopeTreeNode[] = []
  for (const company of companyOptions.value) {
    const node = map.get(company.id)
    if (!node) continue
    const parentId = company.parent_company_id ? Number(company.parent_company_id) : null
    const parent = parentId ? map.get(parentId) : null
    if (parent) {
      parent.children = [...(parent.children || []), node]
    } else {
      roots.push(node)
    }
  }
  for (const node of map.values()) {
    if (node.children?.length) node.children = sortApplicantScopeNodes(node.children)
  }
  return sortApplicantScopeNodes(roots)
}

function buildApplicantDepartmentMemberScopeTree(nodes: any[]): ApplicantScopeTreeNode[] {
  const membersByDepartment = new Map<number | null, EmployeeOption[]>()
  for (const member of memberOptions.value) {
    const departmentId = member.department_id ? Number(member.department_id) : null
    if (!membersByDepartment.has(departmentId)) membersByDepartment.set(departmentId, [])
    membersByDepartment.get(departmentId)!.push(member)
  }
  const memberNode = (member: EmployeeOption): ApplicantScopeTreeNode => {
    const scope: ApplicantScopeValue = { type: 'member', id: member.id, label: member.name }
    const employeeNo = member.employee_no ? `（${member.employee_no}）` : ''
    return {
      key: applicantScopeKey(scope),
      label: `${member.name}${employeeNo}`,
      scope,
    }
  }
  const buildDepartmentMemberNodes = (nodes: any[]): ApplicantScopeTreeNode[] => (nodes || [])
    .map((node) => {
      const scope: ApplicantScopeValue = { type: 'department', id: Number(node.id), label: String(node.name || '') }
      const directMembers = (membersByDepartment.get(Number(node.id)) || [])
        .slice()
        .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'))
        .map(memberNode)
      const children = [...buildDepartmentMemberNodes(node.children || []), ...directMembers]
      return {
        key: applicantScopeKey(scope),
        label: scope.label || `部门#${scope.id}`,
        scope,
        children,
      }
    })
  const unassignedMembers = (membersByDepartment.get(null) || [])
    .slice()
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'))
    .map(memberNode)
  return [
    ...buildDepartmentMemberNodes(departmentTreeData.value),
    ...(unassignedMembers.length ? [{ key: 'member-dept:unassigned', label: '未分配部门', children: unassignedMembers }] : []),
  ]
}

function buildMemberPickerTreeData(): ApplicantScopeTreeNode[] {
  const markDepartmentNodesDisabled = (nodes: ApplicantScopeTreeNode[]): ApplicantScopeTreeNode[] => nodes.map((node) => {
    const scope = node.scope
    if (scope?.type === 'member') return node
    return {
      ...node,
      disabled: true,
      children: node.children ? markDepartmentNodesDisabled(node.children) : [],
    }
  })
  return markDepartmentNodesDisabled(buildApplicantDepartmentMemberScopeTree(departmentTreeData.value))
}

function applicantScopeNodeValue(node: any): ApplicantScopeValue | null {
  return node?.scope || null
}

function normalizeApplicantScopeValues(value: FlowConditionValue): ApplicantScopeValue[] {
  const raw = Array.isArray(value) ? value : value ? [value] : []
  const normalized: ApplicantScopeValue[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (typeof item === 'object' && item && 'type' in item) {
      const type = String(item.type || '') as ApplicantScopeType
      if (!['member', 'department', 'company', 'role'].includes(type)) continue
      const scope: ApplicantScopeValue = {
        type,
        id: item.id === undefined || item.id === null ? undefined : Number(item.id),
        value: item.value === undefined || item.value === null ? undefined : String(item.value),
        label: item.label ? String(item.label) : undefined,
      }
      const key = applicantScopeKey(scope)
      if (seen.has(key)) continue
      seen.add(key)
      normalized.push(scope)
    }
  }
  return normalized
}

function applicantScopeLabel(item: ApplicantScopeValue) {
  if (item.label) return item.label
  if (item.type === 'member') return memberNameById(Number(item.id) || null)
  if (item.type === 'department') return departmentNameById(Number(item.id) || null)
  if (item.type === 'company') return companyNameMap.value[Number(item.id)] || `公司#${item.id}`
  return approvalRoleOptions.find((role) => role.value === item.value)?.label || item.value || '角色'
}

function conditionApplicantScopeValues(condition: FlowConditionEditor) {
  return normalizeApplicantScopeValues(condition.value)
}

function syncApplicantScopeTreeCheckedKeys() {
  if (applicantScopeMode.value !== 'org') return
  const keys = tempApplicantScopeValues.value.map((item) => applicantScopeKey(item))
  applicantScopeTreeRef.value?.setCheckedKeys?.(keys)
}

async function openApplicantScopePicker(condition: FlowConditionEditor) {
  applicantScopeTargetCondition.value = condition
  tempApplicantScopeValues.value = normalizeApplicantScopeValues(condition.value)
  applicantScopeKeyword.value = ''
  applicantScopeMode.value = 'org'
  applicantScopePickerVisible.value = true
  await nextTick()
  syncApplicantScopeTreeCheckedKeys()
}

function closeApplicantScopePicker() {
  applicantScopePickerVisible.value = false
  applicantScopeTargetCondition.value = null
  tempApplicantScopeValues.value = []
}

function isTempApplicantScopeSelected(item: ApplicantScopeValue) {
  const key = applicantScopeKey(item)
  return tempApplicantScopeValues.value.some((selected) => applicantScopeKey(selected) === key)
}

function toggleTempApplicantScopeValue(item: ApplicantScopeValue) {
  const key = applicantScopeKey(item)
  if (isTempApplicantScopeSelected(item)) {
    tempApplicantScopeValues.value = tempApplicantScopeValues.value.filter((selected) => applicantScopeKey(selected) !== key)
  } else {
    tempApplicantScopeValues.value = [...tempApplicantScopeValues.value, item]
  }
  syncApplicantScopeTreeCheckedKeys()
}

function applicantRoleScope(role: { value: string; label: string }): ApplicantScopeValue {
  return { type: 'role', value: role.value, label: role.label }
}

function isApplicantRoleSelected(role: { value: string; label: string }) {
  return isTempApplicantScopeSelected(applicantRoleScope(role))
}

function toggleApplicantRoleScope(role: { value: string; label: string }) {
  toggleTempApplicantScopeValue(applicantRoleScope(role))
}

async function setApplicantScopeMode(mode: ApplicantScopeMode) {
  applicantScopeMode.value = mode
  applicantScopeKeyword.value = ''
  await nextTick()
  syncApplicantScopeTreeCheckedKeys()
}

function removeTempApplicantScopeValue(item: ApplicantScopeValue) {
  const key = applicantScopeKey(item)
  tempApplicantScopeValues.value = tempApplicantScopeValues.value.filter((selected) => applicantScopeKey(selected) !== key)
  syncApplicantScopeTreeCheckedKeys()
}

function filterApplicantScopeNode(keyword: string, node: ApplicantScopeTreeNode) {
  const keywordValue = String(keyword || '').trim().toLowerCase()
  if (!keywordValue) return true
  return String(node?.label || '').toLowerCase().includes(keywordValue)
}

function filterMemberTreeNode(keyword: string, node: ApplicantScopeTreeNode) {
  const keywordValue = String(keyword || '').trim().toLowerCase()
  if (!keywordValue) return true
  if (String(node?.label || '').toLowerCase().includes(keywordValue)) return true
  const scope = applicantScopeNodeValue(node)
  if (scope?.type !== 'member') return true
  return false
}

function memberTreeKey(memberId: number) {
  return applicantScopeKey({ type: 'member', id: memberId })
}

function syncMemberTreeCheckedKeys() {
  if (memberPickerPurpose.value !== 'multi_level_member') return
  const keys = normalizeMemberIds(tempSelectedMemberIds.value).map(memberTreeKey)
  memberTreeRef.value?.setCheckedKeys?.(keys)
}

function onMemberTreeCheck(_: ApplicantScopeTreeNode, checkedInfo: { checkedNodes?: ApplicantScopeTreeNode[] }) {
  const memberIds = (checkedInfo.checkedNodes || [])
    .map((node) => applicantScopeNodeValue(node))
    .filter((item): item is ApplicantScopeValue => Boolean(item && item.type === 'member'))
    .map((item) => Number(item.id))
  tempSelectedMemberIds.value = normalizeMemberIds(memberIds)
  tempSelectedMemberId.value = tempSelectedMemberIds.value[0] || null
  nextTick(syncMemberTreeCheckedKeys)
}

function removeTempSelectedMember(memberId: number) {
  tempSelectedMemberIds.value = tempSelectedMemberIds.value.filter((id) => id !== memberId)
  tempSelectedMemberId.value = tempSelectedMemberIds.value[0] || null
  nextTick(syncMemberTreeCheckedKeys)
}

function onApplicantScopeTreeCheck(_: ApplicantScopeTreeNode, checkedInfo: { checkedNodes?: ApplicantScopeTreeNode[] }) {
  const roleValues = tempApplicantScopeValues.value.filter((item) => item.type === 'role')
  const values = (checkedInfo.checkedNodes || [])
    .map((node) => applicantScopeNodeValue(node))
    .filter((item): item is ApplicantScopeValue => Boolean(item))
  tempApplicantScopeValues.value = normalizeApplicantScopeValues([...roleValues, ...values] as FlowConditionValue)
  nextTick(() => syncApplicantScopeTreeCheckedKeys())
}

function confirmApplicantScopePicker() {
  if (applicantScopeTargetCondition.value) {
    applicantScopeTargetCondition.value.value = tempApplicantScopeValues.value.map((item) => ({
      ...item,
      label: applicantScopeLabel(item),
    }))
    markRulesDirty()
  }
  closeApplicantScopePicker()
}

function conditionOperatorLabel(operator: ConditionOperator) {
  const labels: Record<ConditionOperator, string> = {
    eq: '等于',
    neq: '不等于',
    in: '属于',
    not_in: '不属于',
    contains: '包含',
    not_contains: '不包含',
    empty: '为空',
    not_empty: '不为空',
    lt: '小于',
    lte: '小于等于',
    gt: '大于',
    gte: '大于等于',
    between: '介于',
  }
  return labels[operator]
}

function departmentNameById(departmentId?: number | null) {
  if (!departmentId) return '未分配部门'
  const department = departmentOptions.value.find((item) => item.id === departmentId)
  return department?.name || `部门#${departmentId}`
}

function memberCompactLabel(member?: EmployeeOption | null) {
  if (!member) return ''
  const employeeNo = member.employee_no ? ` · ${member.employee_no}` : ''
  return `${member.name}${employeeNo}`
}

function memberCompactLabelById(memberId?: number | null) {
  if (!memberId) return ''
  return memberCompactLabel(memberOptions.value.find((member) => member.id === memberId))
}

function nodeAssigneeDisplay(node: FlowNodeEditor) {
  if (isParallelBranchNode(node)) {
    const branchCount = node.branches?.length || 0
    return branchCount ? `${branchCount} 条分支并行` : '多条分支同时发起'
  }
  if (isConditionBranchNode(node)) {
    const branchCount = node.branches?.length || 0
    return branchCount ? `${branchCount} 条分支` : '按条件匹配'
  }
  if (node.node_type === 'auto') return node.auto_action === 'reject' ? '系统自动拒绝' : '系统自动通过'
  if (node.node_type === 'notify') {
    const labels = notifySources(node).map((source) => {
      if (source === 'specific_user') return memberNamesForFlowNode(node)
      if (source === 'role') return `${roleLabel(node.role)}角色`
      if (source === 'applicant_self') return '发起人本人'
      if (source === 'applicant_select') return '发起人自选'
      if (source === 'department_head') return '部门主管'
      if (source === 'direct_manager') return '直属主管'
      if (source === 'related_member_field') return '表单联系人'
      if (source === 'form_department_head') return '表单部门主管'
      return '多级主管'
    }).filter(Boolean)
    return labels.length ? labels.join('、') : '请选择抄送人'
  }
  if (node.assignee_source === 'specific_user') return memberNamesForFlowNode(node)
  if (node.assignee_source === 'department_head') return '部门主管'
  if (node.assignee_source === 'multi_level_manager') {
    if (node.multi_level_end_type === 'member') return multiLevelMemberNames(node) ? `审批至${multiLevelMemberNames(node)}` : '审批至指定人员'
    if (node.multi_level_end_type === 'role') return `审批至${roleLabel(node.multi_level_role)}角色主管`
    return node.multi_level_level === 'highest' ? '审批至最高层级主管' : `审批至第${node.multi_level_level || node.manager_level || 1}级主管`
  }
  if (node.assignee_source === 'role') return `${roleLabel(node.role)}角色`
  if (node.assignee_source === 'applicant_self') return '发起人自己'
  if (node.assignee_source === 'applicant_select') {
    if (node.applicant_select_scope === 'selected_members') return `发起人从${normalizeMemberIds(node.applicant_select_member_ids).length || 0}名成员中自选`
    if (node.applicant_select_scope === 'role') return `发起人从${applicantSelectRoleNames(node)}中自选`
    return '发起人自选'
  }
  if (node.assignee_source === 'related_member_field') return '表单内的联系人'
  if (node.assignee_source === 'form_department_head') return '表单内部门主管'
  return '直属上级'
}

function selectFlowNode(uid: string) {
  selectedFlowNodeUid.value = uid
  flowConfigDrawerVisible.value = true
  nodeConfigTab.value = 'assignee'
}

function highlightFlowNode(uid: string) {
  selectedFlowNodeUid.value = uid
  flowConfigDrawerVisible.value = false
  nodeConfigTab.value = 'assignee'
}

function addFlowNode(type: FlowNodeType = 'approval') {
  const newNode = type === 'condition_branch'
    ? createConditionBranchNodeWithPrimaryNodes()
    : createFlowNode(type)
  if (isParallelBranchNode(newNode)) ensureParallelBranches(newNode)
  flowNodes.value.push(newNode)
  highlightFlowNode(newNode.uid)
  markRulesDirty()
}

function insertFlowNodeAt(index: number, type: FlowNodeType = 'approval') {
  const newNode = type === 'condition_branch'
    ? createConditionBranchNodeWithPrimaryNodes(flowNodes.value.splice(index))
    : createFlowNode(type)
  if (isParallelBranchNode(newNode)) ensureParallelBranches(newNode)
  flowNodes.value.splice(index, 0, newNode)
  highlightFlowNode(newNode.uid)
  markRulesDirty()
}

function insertFlowNodeFromCanvas(payload: { branchUid: string | null; index: number; type: FlowNodeType }) {
  const targetNodes = payload.branchUid
    ? findFlowBranchByUid(flowNodes.value, payload.branchUid)?.nodes
    : flowNodes.value
  if (!targetNodes) return
  const newNode = payload.type === 'condition_branch'
    ? createConditionBranchNodeWithPrimaryNodes(targetNodes.splice(payload.index))
    : createFlowNode(payload.type)
  if (isParallelBranchNode(newNode)) ensureParallelBranches(newNode)
  targetNodes.splice(payload.index, 0, newNode)
  highlightFlowNode(newNode.uid)
  markRulesDirty()
}

function removeFlowNode(uid: string) {
  const removed = removeFlowNodeByUid(flowNodes.value, uid)
  if (!removed) return
  normalizeFlowTopologyAfterMutation()
  if (selectedFlowNodeUid.value === uid) {
    selectedFlowNodeUid.value = ''
    flowConfigDrawerVisible.value = false
  }
  markRulesDirty()
}

function onFlowNodeTypeChange(node: FlowNodeEditor) {
  if (isConditionBranchNode(node)) {
    node.assignee_source = 'direct_manager'
    node.approver_id = null
    node.approver_ids = []
    ensureConditionBranches(node)
  } else if (isParallelBranchNode(node)) {
    node.assignee_source = 'direct_manager'
    node.approver_id = null
    node.approver_ids = []
    ensureParallelBranches(node)
  } else if (node.node_type === 'auto') {
    node.approver_id = null
    node.approver_ids = []
    node.assignee_source = 'direct_manager'
    node.role = 'manager'
    node.auto_action = node.auto_action || 'approve'
  } else if (node.node_type === 'notify') {
    node.assignee_source = normalizeAssigneeSource(node.assignee_source || 'specific_user')
    node.notify_sources = node.notify_sources?.length ? node.notify_sources.map(normalizeAssigneeSource) : [node.assignee_source]
    node.branches = undefined
    node.active_branch_uid = undefined
  } else {
    node.assignee_source = normalizeAssigneeSource(node.assignee_source)
    node.branches = undefined
    node.active_branch_uid = undefined
  }
  if (node.assignee_source === 'specific_user') {
    node.approver_ids = flowNodeMemberIds(node)
  }
  if (node.node_type !== 'notify') {
    node.allow_applicant_select = false
    node.include_applicant_self = false
    node.approver_ids = normalizeMemberIds(node.approver_id)
    node.notify_sources = []
  }
  markRulesDirty()
}

function onAssigneeSourceChange(node: FlowNodeEditor) {
  if (node.assignee_source !== 'specific_user') {
    node.approver_id = null
    node.approver_ids = []
  } else {
    node.approver_ids = normalizeMemberIds(node.approver_id)
  }
  if (node.assignee_source !== 'role') {
    node.role = 'manager'
  }
  if (node.assignee_source === 'multi_level_manager') {
    node.approval_mode = 'sequential'
  }
  if (node.assignee_source === 'applicant_select') {
    node.applicant_select_mode = node.applicant_select_mode || 'multiple'
    node.applicant_select_scope = node.applicant_select_scope || 'company'
    node.applicant_select_member_ids = normalizeMemberIds(node.applicant_select_member_ids)
    node.applicant_select_roles = normalizeRoleValues(node.applicant_select_roles)
  }
  if (node.node_type === 'notify') {
    node.notify_sources = [normalizeAssigneeSource(node.assignee_source)]
  }
  markRulesDirty()
}

function openNodeSettings(uid: string) {
  selectFlowNode(uid)
}

function selectFlowBranch(payload: { nodeUid: string; branchUid: string }) {
  const node = findFlowNodeByUid(flowNodes.value, payload.nodeUid)
  if (!node || !isSplitFlowNode(node)) return
  if (isConditionBranchNode(node)) ensureConditionBranches(node)
  if (isParallelBranchNode(node)) ensureParallelBranches(node)
  node.active_branch_uid = payload.branchUid
  selectFlowNode(payload.nodeUid)
}

function closeFlowConfigDrawer() {
  flowConfigDrawerVisible.value = false
}

function changeFlowZoom(delta: number) {
  flowZoom.value = Math.min(120, Math.max(40, flowZoom.value + delta))
}

function resetFlowZoom() {
  flowZoom.value = 100
}

async function centerFlowCanvas() {
  await nextTick()
  const workbench = document.querySelector<HTMLElement>('.approval-flow-workbench')
  if (!workbench) return
  workbench.scrollLeft = Math.max(0, (workbench.scrollWidth - workbench.clientWidth) / 2)
  workbench.scrollTop = 0
}

function addAutomationNode() {
  addFlowNode('auto')
}

function buildDepartmentTree(source: DepartmentOption[]) {
  const map = new Map<number, any>()
  for (const dept of source) {
    map.set(dept.id, { ...dept, children: [] })
  }
  const roots: any[] = []
  for (const dept of source) {
    const node = map.get(dept.id)
    if (dept.parent_id && map.has(dept.parent_id)) {
      map.get(dept.parent_id).children.push(node)
    } else {
      roots.push(node)
    }
  }
  const sortNodes = (nodes: any[]) => {
    nodes.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'))
    nodes.forEach((item) => sortNodes(item.children || []))
  }
  sortNodes(roots)
  return roots
}

function collectDeptAndChildrenIds(departmentId: number) {
  const childrenMap = new Map<number, number[]>()
  for (const dept of departmentOptions.value) {
    if (!dept.parent_id) continue
    if (!childrenMap.has(dept.parent_id)) childrenMap.set(dept.parent_id, [])
    childrenMap.get(dept.parent_id)!.push(dept.id)
  }
  const result = new Set<number>()
  const stack = [departmentId]
  while (stack.length) {
    const current = stack.pop()!
    if (result.has(current)) continue
    result.add(current)
    const children = childrenMap.get(current) || []
    children.forEach((child) => stack.push(child))
  }
  return result
}

function filterMembersByDepartmentAndKeyword(departmentId: number | null, keywordValue: string) {
  const keyword = keywordValue.trim().toLowerCase()
  let base = memberOptions.value
  if (departmentId) {
    const availableDeptIds = collectDeptAndChildrenIds(departmentId)
    base = base.filter((member) => member.department_id && availableDeptIds.has(member.department_id))
  }
  if (!keyword) return base
  return base.filter((member) => {
    const name = String(member.name || '').toLowerCase()
    const employeeNo = String(member.employee_no || '').toLowerCase()
    return name.includes(keyword) || employeeNo.includes(keyword)
  })
}

function filterDepartmentNode(keyword: string, node: any) {
  if (!keyword) return true
  return String(node?.name || '').toLowerCase().includes(keyword.toLowerCase())
}

function onDepartmentTreeSelect(node: any) {
  pickerDepartmentId.value = Number(node?.id) || null
}

function openArchiveApplicantPicker() {
  const currentMember = memberOptions.value.find((member) => member.id === archiveFilters.applicant_id)
  archiveApplicantDepartmentId.value = currentMember?.department_id || null
  archiveApplicantDepartmentKeyword.value = ''
  archiveApplicantKeyword.value = ''
  archiveApplicantPickerVisible.value = true
}

function closeArchiveApplicantPicker() {
  archiveApplicantPickerVisible.value = false
}

function onArchiveDepartmentTreeSelect(node: any) {
  archiveApplicantDepartmentId.value = Number(node?.id) || null
}

function selectArchiveApplicant(member: EmployeeOption) {
  archiveFilters.applicant_id = member.id
  archiveFilters.applicant_keyword = ''
  closeArchiveApplicantPicker()
}

function clearArchiveApplicantFilter() {
  archiveFilters.applicant_id = null
  archiveFilters.applicant_keyword = ''
}

function resetRuleMemberPicker(selectedIds: number[] = []) {
  const currentMember = memberOptions.value.find((member) => member.id === selectedIds[0])
  rulePickerDepartmentId.value = currentMember?.department_id || null
  ruleDepartmentKeyword.value = ''
  ruleMemberKeyword.value = ''
}

function onRuleDepartmentTreeSelect(node: any) {
  rulePickerDepartmentId.value = Number(node?.id) || null
}

function selectedRuleMemberIds() {
  if (activeRuleMemberTarget.value === 'submit') return ruleSettings.submitMemberIds
  if (activeRuleMemberTarget.value === 'templateAdmin') return ruleSettings.templateAdminIds
  return []
}

function setSelectedRuleMemberIds(ids: number[]) {
  const normalized = normalizeMemberIds(ids)
  if (activeRuleMemberTarget.value === 'submit') {
    ruleSettings.submitMemberIds = normalized
    return
  }
  if (activeRuleMemberTarget.value === 'templateAdmin') {
    ruleSettings.templateAdminIds = normalized
  }
}

function isRuleMemberSelected(memberId: number) {
  return selectedRuleMemberIds().includes(memberId)
}

function toggleRuleMember(memberId: number) {
  const selectedIds = selectedRuleMemberIds()
  if (selectedIds.includes(memberId)) {
    setSelectedRuleMemberIds(selectedIds.filter((id) => id !== memberId))
  } else {
    setSelectedRuleMemberIds([...selectedIds, memberId])
  }
}

async function openMemberPicker(targetNode?: FlowNodeEditor | null, purpose: MemberPickerPurpose = 'assignee') {
  const target = targetNode || selectedFlowNode.value
  if (!target) return
  memberPickerPurpose.value = purpose
  memberPickerTargetNode.value = target
  const selectedIds = purpose === 'applicant_select_scope'
    ? normalizeMemberIds(target.applicant_select_member_ids)
    : purpose === 'multi_level_member'
      ? normalizeMemberIds(target.multi_level_member_ids)
    : flowNodeMemberIds(target)
  tempSelectedMemberId.value = selectedIds[0] || null
  tempSelectedMemberIds.value = [...selectedIds]
  const currentMember = memberOptions.value.find((member) => member.id === selectedIds[0])
  pickerDepartmentId.value = currentMember?.department_id || null
  departmentKeyword.value = ''
  memberKeyword.value = ''
  memberPickerVisible.value = true
  await nextTick()
  syncMemberTreeCheckedKeys()
}

function closeMemberPicker() {
  memberPickerVisible.value = false
  memberPickerTargetNode.value = null
  memberPickerPurpose.value = 'assignee'
}

function isTempMemberSelected(memberId: number) {
  return memberPickerMultiple.value
    ? tempSelectedMemberIds.value.includes(memberId)
    : tempSelectedMemberId.value === memberId
}

function toggleTempMember(memberId: number) {
  if (!memberPickerMultiple.value) {
    tempSelectedMemberId.value = memberId
    tempSelectedMemberIds.value = [memberId]
    return
  }
  if (tempSelectedMemberIds.value.includes(memberId)) {
    tempSelectedMemberIds.value = tempSelectedMemberIds.value.filter((id) => id !== memberId)
  } else {
    if (tempSelectedMemberIds.value.length >= 50) {
      ElMessage.warning('指定成员不能超过50人')
      return
    }
    tempSelectedMemberIds.value = [...tempSelectedMemberIds.value, memberId]
  }
  tempSelectedMemberId.value = tempSelectedMemberIds.value[0] || null
}

function confirmMemberPicker() {
  const target = memberPickerTargetNode.value
  if (!target) {
    closeMemberPicker()
    return
  }
  if (memberPickerPurpose.value === 'applicant_select_scope') {
    const selectedIds = normalizeMemberIds(tempSelectedMemberIds.value)
    target.applicant_select_member_ids = selectedIds
  } else if (memberPickerPurpose.value === 'multi_level_member') {
    const selectedIds = normalizeMemberIds(tempSelectedMemberIds.value)
    target.multi_level_member_ids = selectedIds
  } else if (memberPickerMultiple.value) {
    const selectedIds = normalizeMemberIds(tempSelectedMemberIds.value)
    target.approver_ids = selectedIds
    target.approver_id = selectedIds[0] || null
  } else {
    target.approver_id = tempSelectedMemberId.value || null
    target.approver_ids = normalizeMemberIds(target.approver_id)
  }
  markRulesDirty()
  closeMemberPicker()
}

function currentRolePickerValue(node: FlowNodeEditor, field: RolePickerField) {
  if (field === 'multi_level_role') return node.multi_level_role || ''
  if (field === 'applicant_select_roles') return normalizeRoleValues(node.applicant_select_roles)[0] || ''
  return node.role || ''
}

async function openRolePicker(targetNode: FlowNodeEditor, field: RolePickerField = 'role') {
  closeMemberPicker()
  rolePickerTargetNode.value = targetNode
  rolePickerTargetField.value = field
  tempSelectedRole.value = currentRolePickerValue(targetNode, field)
  rolePickerVisible.value = true
  await nextTick()
  if (tempSelectedRole.value) {
    roleTreeRef.value?.setCurrentKey?.(`role-${tempSelectedRole.value}`)
  }
}

function closeRolePicker() {
  rolePickerVisible.value = false
  rolePickerTargetNode.value = null
  tempSelectedRole.value = ''
  rolePickerTargetField.value = 'role'
}

function onRoleTreeSelect(data: RoleTreeNode) {
  if (!data.role) return
  tempSelectedRole.value = data.role.value
}

function confirmRolePicker() {
  const target = rolePickerTargetNode.value
  if (!target || !tempSelectedRole.value) {
    closeRolePicker()
    return
  }
  if (rolePickerTargetField.value === 'multi_level_role') {
    target.multi_level_role = tempSelectedRole.value
  } else if (rolePickerTargetField.value === 'applicant_select_roles') {
    target.applicant_select_roles = [tempSelectedRole.value]
  } else {
    target.role = tempSelectedRole.value
  }
  markRulesDirty()
  closeRolePicker()
}

function parseRuleJson(value: unknown): Record<string, any> {
  if (!value) return {}
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      if (parsed && typeof parsed === 'object') return parsed as Record<string, any>
      return {}
    } catch {
      return {}
    }
  }
  if (typeof value === 'object') return value as Record<string, any>
  return {}
}

function applyRuleSettings(permissionRules: any) {
  normalizeRuleSettings()
  const permission = parseRuleJson(permissionRules)
  const visibleScope = permission.visible_scope || {}
  ruleSettings.visibleDepartmentIds = [...(visibleScope.department_ids || [])]
  ruleSettings.includeSubDepartments = Boolean(visibleScope.include_sub_departments)

  const submitPermission = permission.submit_permission || {}
  ruleSettings.submitPermissionType = submitPermission.type || 'all'
  ruleSettings.submitRoles = [...(submitPermission.roles || [])]
  ruleSettings.submitMemberIds = normalizeMemberIds(submitPermission.member_ids || submitPermission.employee_ids)

  const viewPermission = permission.view_permission || {}
  ruleSettings.viewPermissionType = viewPermission.type || 'all'

  const templateManagement = permission.template_management || {}
  ruleSettings.templateAdminType = templateManagement.type === 'selected_admins' ? 'selected' : 'all'
  ruleSettings.templateAdminIds = normalizeMemberIds(
    templateManagement.admin_ids || templateManagement.member_ids || templateManagement.employee_ids,
  )

  const fieldPermissions = permission.field_edit_permissions || {}
  ruleSettings.fixedApproverLocked = !Boolean(fieldPermissions.fixed_approver?.modifiable)
  ruleSettings.fixedCcLocked = !Boolean(fieldPermissions.fixed_cc?.modifiable)
  ruleSettings.fixedHandlerLocked = !Boolean(fieldPermissions.fixed_handler?.modifiable)

  const actions = permission.in_progress_actions || {}
  ruleSettings.approvalCommentRequired = Boolean(actions.approval_comment_required)
  ruleSettings.handlerCommentRequired = Boolean(actions.handler_comment_required)
}

function buildFlowNodesFromServer(rawNodes: any[]) {
  const roleAlias = ['admin', 'hr', 'finance', 'manager', 'asset_admin']
  const normalizeServerCondition = (condition: any): FlowConditionEditor => {
    const field = normalizeConditionField(condition?.field || 'applicant.scope')
    return {
      uid: uniqueUid('condition'),
      field,
      operator: (condition?.operator || condition?.op || defaultOperatorForConditionField(field)) as ConditionOperator,
      value: condition?.value ?? defaultValueForConditionField(field),
    }
  }
  const normalizeServerConditionGroups = (branch: any): FlowConditionGroupEditor[] => {
    const rawGroups = branch?.condition_groups || branch?.groups || []
    if (Array.isArray(rawGroups) && rawGroups.length) {
      return rawGroups.map((group: any) => ({
        uid: uniqueUid('condition_group'),
        label: group.label || group.group_label || '条件组',
        condition_combinator: group.condition_combinator === 'or' ? 'or' : 'and',
        conditions: (group.conditions || []).map((condition: any) => normalizeServerCondition(condition)),
      }))
    }
    const legacyConditions = (branch?.conditions || []).map((condition: any) => normalizeServerCondition(condition))
    return legacyConditions.length
      ? [{
        uid: uniqueUid('condition_group'),
        label: '条件组',
        condition_combinator: branch?.condition_combinator === 'or' ? 'or' : 'and',
        conditions: legacyConditions,
      }]
      : []
  }
  const normalizeServerNode = (node: any): FlowNodeEditor => {
    const conditionMeta = parseRuleJson(node.condition_rules)
    const rawNodeType = String(node.node_type || 'approval')
    const nodeType = (rawNodeType === 'condition' ? 'condition_branch' : rawNodeType) as FlowNodeType
    if (nodeType === 'condition_branch' || nodeType === 'parallel_branch') {
      const branches = (conditionMeta.branches || node.branches || []).map((branch: any, branchIndex: number) => {
        const conditionGroups = nodeType === 'condition_branch' ? normalizeServerConditionGroups(branch) : []
        return {
          uid: uniqueUid('branch'),
          label: branch.label || branch.branch_label || (nodeType === 'condition_branch' ? (branch.is_default_branch ? '默认条件' : `条件${branchIndex + 1}`) : `分支${branchIndex + 1}`),
          is_default_branch: nodeType === 'condition_branch' ? Boolean(branch.is_default_branch) : false,
          condition_combinator: conditionGroups[0]?.condition_combinator || (branch.condition_combinator === 'or' ? 'or' : 'and'),
          conditions: conditionGroups[0]?.conditions || [],
          condition_group_combinator: branch.condition_group_combinator === 'and' ? 'and' : 'or',
          condition_groups: conditionGroups,
          nodes: (branch.nodes || []).map((child: any) => normalizeServerNode(child)),
        }
      }) as FlowBranchEditor[]
      const branchNode = createFlowNode(nodeType)
      branchNode.uid = uniqueUid('server_node')
      branchNode.branches = branches.length ? branches : branchNode.branches
      branchNode.active_branch_uid = branchNode.branches?.[0]?.uid
      if (nodeType === 'condition_branch') ensureConditionBranches(branchNode)
      if (nodeType === 'parallel_branch') ensureParallelBranches(branchNode)
      return branchNode
    }
    const approverType = conditionMeta.approver_type || node.approver_type || 'direct_manager'
    const source = conditionMeta.assignee_source || approverType
    const memberIds = normalizeMemberIds(conditionMeta.member_ids || node.member_ids)
    const approverId = Number(node.approver_id || memberIds[0]) || null
    const normalizedSource = roleAlias.includes(source) ? 'role' : normalizeAssigneeSource(source)
    const notifySources = Array.isArray(conditionMeta.notify_sources)
      ? conditionMeta.notify_sources.map(normalizeAssigneeSource)
      : (nodeType === 'notify' ? [normalizedSource] : [])
    return {
      uid: uniqueUid('server_node'),
      node_type: nodeType,
      assignee_source: normalizedSource,
      approver_id: approverId,
      approver_ids: normalizeMemberIds([...(memberIds || []), approverId]),
      role: conditionMeta.role || (roleAlias.includes(source) ? source : 'manager'),
      approval_mode: conditionMeta.approval_mode || (normalizedSource === 'multi_level_manager' ? 'sequential' : 'or_sign'),
      allow_applicant_select: Boolean(conditionMeta.allow_applicant_select),
      include_applicant_self: Boolean(conditionMeta.include_applicant_self),
      applicant_select_mode: conditionMeta.applicant_select_mode === 'single' ? 'single' : 'multiple',
      applicant_select_scope: applicantSelectScopeValue(conditionMeta.applicant_select_scope),
      applicant_select_member_ids: normalizeMemberIds(conditionMeta.applicant_select_member_ids),
      applicant_select_roles: normalizeRoleValues(conditionMeta.applicant_select_roles || conditionMeta.applicant_select_role),
      notify_sources: notifySources,
      related_field_code: conditionMeta.related_field_code || '',
      related_member_field_code: conditionMeta.related_member_field_code || (normalizedSource === 'related_member_field' ? conditionMeta.related_field_code || '' : ''),
      related_department_field_code: conditionMeta.related_department_field_code || (normalizedSource === 'form_department_head' ? conditionMeta.related_field_code || '' : ''),
      manager_level: Number(conditionMeta.manager_level || 1),
      fallback_to_upper_manager: conditionMeta.fallback_to_upper_manager !== false,
      applicant_self_if_head: Boolean(conditionMeta.applicant_self_if_head),
      multi_level_end_type: conditionMeta.multi_level_end_type === 'member' ? 'member' : conditionMeta.multi_level_end_type === 'role' ? 'role' : 'level',
      multi_level_role: conditionMeta.multi_level_role || conditionMeta.role || 'manager',
      multi_level_member_ids: normalizeMemberIds(conditionMeta.multi_level_member_ids),
      multi_level_limit_enabled: Boolean(conditionMeta.multi_level_limit_enabled),
      multi_level_limit_level: Number(conditionMeta.multi_level_limit_level || 1),
      multi_level_level: conditionMeta.multi_level_level || 'highest',
      empty_action: conditionMeta.empty_action || '',
      empty_member_id: Number(conditionMeta.empty_member_id) || null,
      vote_pass_count: Number(conditionMeta.vote_pass_count || 1),
      auto_action: conditionMeta.auto_action === 'reject' ? 'reject' : 'approve',
    }
  }
  const normalized = (rawNodes || [])
    .sort((a, b) => Number(a.node_order || 0) - Number(b.node_order || 0))
    .map((node) => normalizeServerNode(node))
    .filter((node) => ['approval', 'notify', 'handler', 'auto', 'condition_branch', 'parallel_branch'].includes(node.node_type))
  flowNodes.value = ensureDefaultManualNodeTypes(normalized.length ? normalized : defaultFlowNodes())
  normalizeFlowTopologyAfterMutation()
  selectedFlowNodeUid.value = flowNodes.value[0]?.uid || ''
}

async function loadFlowNodesByBusinessCode(businessCode: string) {
  try {
    const flows: any[] = await get('/approval/flows', { module: businessCode, is_active: true })
    const flow = (flows || []).find((item) => item.module === businessCode) || flows?.[0]
    buildFlowNodesFromServer(flow?.nodes || [])
  } catch {
    flowNodes.value = defaultFlowNodes()
    selectedFlowNodeUid.value = flowNodes.value[0]?.uid || ''
  }
}

async function hydrateTemplateFromApi(templateId: number) {
  try {
    const detail: any = await get(`/approval/types/${templateId}`, undefined, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    const fields = Array.isArray(detail?.form_fields) ? detail.form_fields : []
    currentTemplate.businessCode = canonicalTemplateBusinessCode({
      name: detail?.name || currentTemplate.name,
      business_code: detail?.business_code || currentTemplate.businessCode,
    })
    const videoCatalogItem = videoTemplateCatalogItem({
      name: detail?.name || currentTemplate.name,
      business_code: detail?.business_code || currentTemplate.businessCode,
    })
    if (videoCatalogItem) {
      currentTemplate.name = videoCatalogItem.name
    }
    currentTemplate.group = templateCategoryNameForItem({
      id: detail?.id,
      name: detail?.name || currentTemplate.name,
      business_code: detail?.business_code || currentTemplate.businessCode,
      category: detail?.category,
      categoryKey: detail?.category_key,
      is_active: detail?.is_active !== false,
    })
    editableFields.value = fields.length
      ? normalizeFieldsForTemplate({
        name: currentTemplate.name,
        business_code: currentTemplate.businessCode,
      }, fields)
      : cloneData(defaultFieldsForTemplate(currentTemplate.name))
    selectedFieldCode.value = preferredSelectedFieldCode(currentTemplate.name, editableFields.value)
    selectedDetailChildCode.value = ''
    detailChildTargetCode.value = ''
    applyRuleSettings(detail.permission_rules)
    currentTemplate.isActive = detail.is_active !== false
    currentTemplate.status = detail.status || (currentTemplate.isActive ? 'enabled' : 'draft')
    currentTemplate.version = Number(detail.version || currentTemplate.version || 1)
    currentTemplate.createdAt = detail.updated_at || detail.created_at || currentTemplate.createdAt || ''
    currentTemplate.description = String(detail.description || '').slice(0, 100)
    await loadFlowNodesByBusinessCode(currentTemplate.businessCode || templateBusinessCode())
    await loadTemplateVersions(templateId)
    currentFlowVersionMode.value = 'published'
    selectedFlowVersionId.value = flowVersions.value[0]?.id || null
    const draft = await get(`/field/template-drafts/${templateId}`).catch(() => ({data:null}))
    draftTemplateSnapshot.value = draft.data || null
  } catch {
    editableFields.value = []
    selectedFieldCode.value = ''
    selectedDetailChildCode.value = ''
    detailChildTargetCode.value = ''
    flowNodes.value = defaultFlowNodes()
    resetFlowVersionState('published')
    ElMessage.error('模板读取失败，请稍后重试')
  }
}

function ensureTemplateConfigAccess() {
  if (canManageTemplateConfigs.value) {
    return true
  }
  ElMessage.warning('当前角色仅可查看模板列表，模板配置仅对管理员和 HR 开放')
  return false
}

async function handleTemplateSelection(item: TemplateMenuItem) {
  if (!canManageTemplateConfigs.value) {
    return
  }
  if (templateBatchMode.value) {
    toggleTemplateSelectionByItem(item)
    return
  }
  await openTemplate(item)
}

async function openTemplate(input: string | TemplateMenuItem) {
  if (!ensureTemplateConfigAccess()) {
    return
  }
  const item = typeof input === 'string'
    ? templateGroups.value.flatMap((group) => group.items).find((candidate) => candidate.name === input)
    : input
  if (!item?.id) {
    const templateName = item?.name || (typeof input === 'string' ? input : '')
    if (catalogItemForTemplate({ name: templateName })) {
      await startRecommendedTemplate(templateName)
      return
    }
    ElMessage.warning('模板尚未保存，请先从添加模板初始化')
    return
  }
  addDialogVisible.value = false
  closeControlLibrary()
  currentTemplate.id = item?.id || null
  currentTemplate.name = item?.name || (typeof input === 'string' ? input : '未命名模板')
  currentTemplate.group = item ? templateGroups.value.find((group) => group.items.some((candidate) => candidate.name === item.name))?.name || '其他' : '其他'
  currentTemplate.businessCode = canonicalTemplateBusinessCode({
    name: currentTemplate.name,
    business_code: item?.business_code,
  })
  currentTemplate.isActive = item?.is_active !== false
  currentTemplate.status = item?.status || (currentTemplate.isActive ? 'enabled' : 'draft')
  currentTemplate.version = Number(item?.version || 1)
  currentTemplate.createdAt = item?.updated_at || item?.created_at || ''
  currentTemplate.description = String(item?.description || item?.note || '').slice(0, 100)
  settingsTab.value = 'template'
  editorStep.value = 'basic'
  screen.value = 'settings'
  rulesDirty.value = false
  await hydrateTemplateFromApi(item.id)
  if ((settingsTab.value as SettingsTab) === 'records') {
    loadCurrentTemplateRecords()
  }
}

function allSavedTemplates() {
  return templateGroups.value.flatMap((group) => group.items)
}

async function batchDisableSelectedTemplates() {
  if (!ensureTemplateConfigAccess()) return
  if (!templateBatchMode.value) {
    templateBatchMode.value = true
    ElMessage.info('请选择需要停用的表单')
    return
  }
  const selectedItems = allSavedTemplates().filter((item) => (
    item.id && selectedTemplateIds.value.includes(item.id) && item.is_active !== false
  ))
  if (!selectedItems.length) {
    ElMessage.warning('请选择需要停用的已启用表单')
    return
  }
  try {
    await ElMessageBox.confirm(`确定停用已选择的 ${selectedItems.length} 个审批表单吗？停用后员工将不能发起这些申请。`, '批量停用', {
      type: 'warning',
      confirmButtonText: '停用',
      cancelButtonText: '取消',
    })
    try {
      await post('/approval/types/bulk-disable', {
        ids: selectedItems.map((item) => item.id).filter(Boolean),
      }, {
        skipErrorReport: true,
        skipDefaultErrorHandler: true,
      })
    } catch {
      await Promise.all(selectedItems.map((item) => put(`/approval/types/${item.id}`, {
        is_active: false,
        status: 'disabled',
      }, {
        skipErrorReport: true,
        skipDefaultErrorHandler: true,
      })))
    }
    selectedTemplateIds.value = []
    templateBatchMode.value = false
    await loadTemplateGroups()
    ElMessage.success('已停用所选表单')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel' || error?.action === 'close') {
      return
    }
    ElMessage.error(error?.response?.data?.detail || '批量停用失败，请稍后重试')
  }
}

async function createTemplateGroup() {
  if (!ensureTemplateConfigAccess()) return
  try {
    const result = await ElMessageBox.prompt('请输入分组名称', '新建分组', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputPattern: /^.{1,20}$/,
      inputErrorMessage: '分组名称需为 1-20 个字符',
    })
    const groupName = String(result.value || '').trim()
    if (!groupName) return
    if (templateGroups.value.some((group) => group.name === groupName)) {
      ElMessage.warning('该分组已存在')
      focusTemplateCategory(groupName)
      return
    }
    templateGroups.value = sortTemplateGroups([...templateGroups.value, { name: groupName, items: [] }])
    focusTemplateCategory(groupName)
    ElMessage.success('分组已创建，可在模板基础设置中选择该分组')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel' || error?.action === 'close') {
      return
    }
    ElMessage.error('新建分组失败，请稍后重试')
  }
}

function templateSortKey(item: TemplateMenuItem) {
  return item.id ? `template:${item.id}` : `draft:${item.name}`
}

function sortTemplateGroup(groupName: string) {
  if (!ensureTemplateConfigAccess()) return
  const group = templateGroups.value.find((item) => item.name === groupName)
  if (!group) return
  templateSortMode.value = 'templates'
  templateSortGroupName.value = groupName
  templateSortDraftItems.value = group.items.map((item) => ({
    key: templateSortKey(item),
    label: item.name,
    subtitle: item.id ? templateUpdatedText(item) : '未创建表单，保存后才会持久排序',
    disabled: !item.id,
  }))
  templateSortDialogVisible.value = true
}

function sortAllTemplateGroups() {
  if (!ensureTemplateConfigAccess()) return
  templateSortMode.value = 'groups'
  templateSortGroupName.value = ''
  templateSortDraftItems.value = templateGroups.value.map((group) => {
    const savedCount = group.items.filter((item) => item.id).length
    return {
      key: group.name,
      label: group.name,
      subtitle: `${savedCount} 个已保存表单 / 共 ${group.items.length} 个显示项`,
      savedCount,
      disabled: savedCount === 0,
    }
  })
  templateSortDialogVisible.value = true
}

function moveTemplateSortItem(index: number, direction: -1 | 1) {
  const nextIndex = index + direction
  if (nextIndex < 0 || nextIndex >= templateSortDraftItems.value.length) return
  const items = [...templateSortDraftItems.value]
  const current = items[index]
  items[index] = items[nextIndex]
  items[nextIndex] = current
  templateSortDraftItems.value = items
}

function closeTemplateSortDialog() {
  if (templateSortSaving.value) return
  templateSortDialogVisible.value = false
  templateSortGroupName.value = ''
  templateSortDraftItems.value = []
}

function applyTemplateSortDraftToGroups() {
  if (templateSortMode.value === 'groups') {
    const groupMap = new Map(templateGroups.value.map((group) => [group.name, group]))
    const orderedGroups = templateSortDraftItems.value
      .map((item) => groupMap.get(item.key))
      .filter((group): group is TemplateGroup => Boolean(group))
    const orderedNames = new Set(orderedGroups.map((group) => group.name))
    return [
      ...orderedGroups,
      ...templateGroups.value.filter((group) => !orderedNames.has(group.name)),
    ]
  }

  const targetGroupName = templateSortGroupName.value
  const targetGroup = templateGroups.value.find((group) => group.name === targetGroupName)
  if (!targetGroup) return templateGroups.value
  const itemMap = new Map(targetGroup.items.map((item) => [templateSortKey(item), item]))
  const orderedItems = templateSortDraftItems.value
    .map((item) => itemMap.get(item.key))
    .filter((item): item is TemplateMenuItem => Boolean(item))
  const orderedKeys = new Set(orderedItems.map(templateSortKey))
  return templateGroups.value.map((group) => (
    group.name === targetGroupName
      ? { ...group, items: [...orderedItems, ...group.items.filter((item) => !orderedKeys.has(templateSortKey(item)))] }
      : group
  ))
}

async function persistTemplateGroupsOrder(groups: TemplateGroup[]) {
  const updates: Array<Promise<unknown>> = []
  groups.forEach((group, groupIndex) => {
    group.items.forEach((item, itemIndex) => {
      if (!item.id) return
      const nextSortOrder = (groupIndex + 1) * 1000 + (itemIndex + 1) * 10
      item.sort_order = nextSortOrder
      updates.push(put(`/approval/types/${item.id}`, {
        sort_order: nextSortOrder,
      }, {
        skipErrorReport: true,
        skipDefaultErrorHandler: true,
      }))
    })
  })
  if (!updates.length) {
    ElMessage.warning('当前没有已保存表单可排序')
    return false
  }
  await Promise.all(updates)
  return true
}

async function confirmTemplateSort() {
  if (!ensureTemplateConfigAccess()) return
  const nextGroups = applyTemplateSortDraftToGroups()
  templateSortSaving.value = true
  try {
    const saved = await persistTemplateGroupsOrder(nextGroups)
    if (!saved) return
    templateGroups.value = nextGroups
    syncTemplateGroupUiState(nextGroups)
    await loadTemplateGroups()
    if (templateSortMode.value === 'templates' && templateSortGroupName.value) {
      focusTemplateCategory(templateSortGroupName.value)
    }
    ElMessage.success(templateSortMode.value === 'groups' ? '分组顺序已保存' : '表单顺序已保存')
    templateSortDialogVisible.value = false
    templateSortGroupName.value = ''
    templateSortDraftItems.value = []
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '排序保存失败，请稍后重试')
  } finally {
    templateSortSaving.value = false
  }
}

function configureTemplateGroup(groupName: string) {
  focusTemplateCategory(groupName)
  ElMessage.info('分组配置会随模板基础设置保存；请选择表单后可调整所属分组')
}

async function exportTemplate(item: TemplateMenuItem) {
  if (!item.id) {
    ElMessage.warning('模板尚未创建，无法导出')
    return
  }
  try {
    let detail: any
    try {
      detail = await get(`/approval/types/${item.id}/export`, undefined, {
        skipErrorReport: true,
        skipDefaultErrorHandler: true,
      })
    } catch {
      detail = await get(`/approval/types/${item.id}`, undefined, {
        skipErrorReport: true,
        skipDefaultErrorHandler: true,
      })
    }
    const payload = JSON.stringify(detail, null, 2)
    const blob = new Blob([payload], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${item.name.replace(/[\\/:*?"<>|]/g, '_')}-审批表单.json`
    link.click()
    URL.revokeObjectURL(url)
    ElMessage.success('模板配置已导出')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '模板导出失败，请稍后重试')
  }
}

function templateMoreKey(item: TemplateMenuItem) {
  return `${item.id || 'draft'}:${item.business_code || item.name}`
}

function isTemplateMoreOpen(item: TemplateMenuItem) {
  return activeTemplateMoreKey.value === templateMoreKey(item)
}

function closeTemplateMoreMenu() {
  activeTemplateMoreKey.value = ''
}

function toggleTemplateMore(item: TemplateMenuItem) {
  if (!ensureTemplateConfigAccess()) return
  const key = templateMoreKey(item)
  activeTemplateMoreKey.value = activeTemplateMoreKey.value === key ? '' : key
}

function handleTemplateDocumentClick() {
  closeTemplateMoreMenu()
}

function handleTemplateMoreAction(item: TemplateMenuItem, action: TemplateMoreAction) {
  closeTemplateMoreMenu()
  if (action === 'copy') {
    void copyTemplateFromMore(item)
    return
  }
  if (action === 'move') {
    openMoveTemplateDialog(item)
    return
  }
  void disableTemplateFromMore(item)
}

function templateCurrentGroupForItem(item: TemplateMenuItem) {
  return templateGroups.value.find((group) => group.items.some((candidate) => (
    item.id ? candidate.id === item.id : candidate.name === item.name
  )))?.name || templateCategoryNameForItem(item) || '其他'
}

function templateCopyBusinessCode(item: TemplateMenuItem) {
  const sourceCode = canonicalTemplateBusinessCode({
    name: item.name,
    business_code: item.business_code,
  }).replace(/[^\w]/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '')
  return `${sourceCode || 'custom_template'}_copy_${Date.now().toString(36)}`.slice(0, 100)
}

async function copyTemplateFromMore(item: TemplateMenuItem) {
  if (!ensureTemplateConfigAccess()) return
  if (!item.id) {
    ElMessage.warning('请先初始化该表单，再复制配置')
    return
  }
  const name = `副本 ${item.name}`.slice(0, 100)
  const businessCode = templateCopyBusinessCode(item)
  try {
    const copied: any = await post(`/approval/types/${item.id}/copy`, {
      name,
      business_code: businessCode,
    }, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    await loadTemplateGroups()
    const copiedItem = allSavedTemplates().find((candidate) => candidate.id === copied?.id)
      || templateMenuItemFromApi(copied)
    if (copiedItem) {
      await openTemplate(copiedItem)
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '复制表单失败，请稍后重试')
  }
}

function openMoveTemplateDialog(item: TemplateMenuItem) {
  if (!ensureTemplateConfigAccess()) return
  if (!item.id) {
    ElMessage.warning('请先初始化该表单，再移动分组')
    return
  }
  const currentGroup = templateCurrentGroupForItem(item)
  movingTemplate.value = item
  templateMoveCurrentGroup.value = currentGroup
  templateMoveTargetGroup.value = ''
  templateMoveNewGroupMode.value = false
  templateMoveNewGroupName.value = ''
  templateMoveDialogVisible.value = true
}

function closeTemplateMoveDialog() {
  templateMoveDialogVisible.value = false
  movingTemplate.value = null
  templateMoveCurrentGroup.value = ''
  templateMoveTargetGroup.value = ''
  templateMoveNewGroupMode.value = false
  templateMoveNewGroupName.value = ''
}

function selectTemplateMoveGroup(groupName: string) {
  if (groupName === templateMoveCurrentGroup.value) return
  templateMoveTargetGroup.value = groupName
}

function startTemplateMoveNewGroup() {
  templateMoveNewGroupMode.value = true
  templateMoveNewGroupName.value = ''
  void nextTick(() => {
    if (typeof document !== 'undefined') {
      document.querySelector<HTMLInputElement>('.template-move-new-row input')?.focus()
    }
  })
}

function cancelTemplateMoveNewGroup() {
  templateMoveNewGroupMode.value = false
  templateMoveNewGroupName.value = ''
}

function confirmTemplateMoveNewGroup() {
  const groupName = templateMoveNewGroupName.value.trim()
  if (!groupName) {
    ElMessage.warning('请输入分组名称')
    return
  }
  if (groupName.length > 30) {
    ElMessage.warning('分组名称不能超过 30 个字符')
    return
  }
  if (!templateGroups.value.some((group) => group.name === groupName)) {
    templateGroups.value = sortTemplateGroups([...templateGroups.value, { name: groupName, items: [] }])
    syncTemplateGroupUiState(templateGroups.value)
  }
  if (groupName === templateMoveCurrentGroup.value) {
    ElMessage.warning('表单已在该分组中')
    return
  }
  templateMoveTargetGroup.value = groupName
  templateMoveNewGroupMode.value = false
  templateMoveNewGroupName.value = ''
}

async function confirmTemplateMove() {
  if (!ensureTemplateConfigAccess()) return
  const item = movingTemplate.value
  const targetGroup = templateMoveTargetGroup.value
  if (!item?.id) {
    ElMessage.warning('未找到需要移动的表单')
    return
  }
  if (!targetGroup) {
    ElMessage.warning('请选择目标分组')
    return
  }
  if (targetGroup === templateMoveCurrentGroup.value) {
    closeTemplateMoveDialog()
    return
  }
  try {
    const catalogItem = catalogItemForTemplate(item)
    await put(`/approval/types/${item.id}`, {
      category: targetGroup,
      category_key: templateCategoryKeyForPayload(targetGroup, catalogItem),
    }, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    if (currentTemplate.id === item.id) {
      currentTemplate.group = targetGroup
    }
    closeTemplateMoveDialog()
    await loadTemplateGroups()
    focusTemplateCategory(targetGroup)
    ElMessage.success('已移动到目标分组')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '移动分组失败，请稍后重试')
  }
}

async function disableTemplateFromMore(item: TemplateMenuItem) {
  if (!ensureTemplateConfigAccess()) return
  if (!item.id) {
    ElMessage.warning('模板尚未创建，无法停用')
    return
  }
  if (item.is_active === false) return
  try {
    await ElMessageBox.confirm(`确定停用「${item.name}」吗？停用后员工将不能发起该申请。`, '停用表单', {
      type: 'warning',
      confirmButtonText: '停用',
      cancelButtonText: '取消',
    })
    await toggleTemplateStatus(item, false)
  } catch (error: any) {
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel' || error?.action === 'close') {
      return
    }
    ElMessage.error(error?.response?.data?.detail || '停用表单失败，请稍后重试')
  }
}

function savedTemplateByBusinessCode(businessCode: string) {
  const targetCatalogItem = catalogItemForTemplate({ business_code: businessCode })
  return allSavedTemplates().find((item) => {
    if (!item.id) return false
    if (item.business_code === businessCode) return true
    if (!targetCatalogItem) return false
    return catalogItemForTemplate(item)?.name === targetCatalogItem.name
  })
}

function recommendedGroupName(name: string) {
  const group = recommendedTemplateGroups.find((candidate) => candidate.items.some((item) => item.name === name))
  return group?.name || (catalogItemForTemplate({ name })?.category ?? '其他')
}

async function startRecommendedTemplate(name: string) {
  const businessCode = templateBusinessCodeForName(name)
  const existingTemplate = savedTemplateByBusinessCode(businessCode)
  if (existingTemplate) {
    await openTemplate(existingTemplate)
    return
  }

  addDialogVisible.value = false
  currentTemplate.id = null
  currentTemplate.name = name
  currentTemplate.group = recommendedGroupName(name)
  currentTemplate.businessCode = businessCode
  currentTemplate.isActive = true
  currentTemplate.status = 'draft'
  currentTemplate.version = 1
  currentTemplate.createdAt = ''
  currentTemplate.description = ''
  settingsTab.value = 'template'
  editorStep.value = 'basic'
  resetEditableFields(name)
  normalizeRuleSettings()
  flowNodes.value = defaultFlowNodes()
  selectedFlowNodeUid.value = flowNodes.value[0]?.uid || ''
  rulesDirty.value = false
  resetFlowVersionState('draft')
  screen.value = 'settings'
}

async function startBlankTemplate() {
  addDialogVisible.value = false
  currentTemplate.id = null
  currentTemplate.name = '未命名模板'
  currentTemplate.group = '其他'
  currentTemplate.businessCode = templateBusinessCodeForName('未命名模板')
  currentTemplate.isActive = true
  currentTemplate.status = 'draft'
  currentTemplate.version = 1
  currentTemplate.createdAt = ''
  currentTemplate.description = ''
  settingsTab.value = 'template'
  editorStep.value = 'basic'
  resetEditableFields('未命名模板')
  normalizeRuleSettings()
  flowNodes.value = defaultFlowNodes()
  selectedFlowNodeUid.value = flowNodes.value[0]?.uid || ''
  rulesDirty.value = false
  resetFlowVersionState('draft')
  const draft = await get('/field/template-drafts/0').catch(() => ({data:null}))
  draftTemplateSnapshot.value = draft.data || null
  screen.value = 'settings'
}

function startCopyTemplate() {
  addDialogVisible.value = false
  const firstTemplate = allSavedTemplates().find((item) => item.id)
  if (!firstTemplate) {
    ElMessage.info('暂无可复制模板')
    return
  }
  openTemplate(firstTemplate)
}

function switchSettingsTab(tab: SettingsTab) {
  closeControlLibrary()
  settingsTab.value = tab
  if (tab === 'template') editorStep.value = 'basic'
  if (tab === 'records') {
    loadCurrentTemplateRecords()
  }
}

function switchEditorStep(step: EditorStep) {
  closeControlLibrary()
  settingsTab.value = 'template'
  editorStep.value = step
  if (step === 'flow') {
    flowZoom.value = 100
    centerFlowCanvas()
  }
}

function returnToApprovalOverview() {
  screen.value = 'overview'
  loadOverviewArchive()
}

function markRulesDirty() {
  rulesDirty.value = true
  currentFlowVersionMode.value = 'draft'
  selectedFlowVersionId.value = null
}

function cloneData<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function captureTemplateEditorSnapshot(): TemplateEditorSnapshot {
  return {
    template: {
      id: currentTemplate.id,
      name: currentTemplate.name,
      group: currentTemplate.group,
      businessCode: currentTemplate.businessCode,
      isActive: currentTemplate.isActive,
      status: currentTemplate.status,
      version: currentTemplate.version,
      createdAt: currentTemplate.createdAt,
      description: currentTemplate.description,
    },
    fields: cloneData(editableFields.value),
    ruleSettings: cloneData(ruleSettings),
    flowNodes: cloneData(flowNodes.value),
    selectedFlowNodeUid: selectedFlowNodeUid.value,
    rulesDirty: rulesDirty.value,
  }
}

function restoreTemplateEditorSnapshot(snapshot: TemplateEditorSnapshot) {
  currentTemplate.id = snapshot.template.id
  currentTemplate.name = snapshot.template.name
  currentTemplate.group = snapshot.template.group
  currentTemplate.businessCode = snapshot.template.businessCode
  currentTemplate.isActive = snapshot.template.isActive
  currentTemplate.status = snapshot.template.status
  currentTemplate.version = snapshot.template.version
  currentTemplate.createdAt = snapshot.template.createdAt
  currentTemplate.description = snapshot.template.description || ''
  editableFields.value = cloneData(snapshot.fields)
  normalizeRuleSettings()
  Object.assign(ruleSettings, cloneData(snapshot.ruleSettings))
  flowNodes.value = cloneData(snapshot.flowNodes)
  selectedFlowNodeUid.value = snapshot.selectedFlowNodeUid || flowNodes.value[0]?.uid || ''
  rulesDirty.value = snapshot.rulesDirty
}

function resetFlowVersionState(mode: FlowVersionMode = 'published') {
  flowVersionMenuVisible.value = false
  currentFlowVersionMode.value = mode
  selectedFlowVersionId.value = null
  draftTemplateSnapshot.value = null
  flowVersions.value = []
}

function versionStatusLabel(version: FlowVersionItem) {
  if (version.status === 'draft') return '草稿'
  return version.id === latestFlowVersion.value?.id ? '启用中' : '已发布'
}

function formatFlowVersionTime(value?: string) {
  const formatted = formatApprovalDateTime(value)
  if (formatted === '-') return '-'
  return formatted.slice(5)
}

async function loadTemplateVersions(templateId: number | null) {
  if (!templateId) {
    flowVersions.value = []
    selectedFlowVersionId.value = null
    return
  }
  try {
    const versions: any[] = await get(`/approval/types/${templateId}/versions`, undefined, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    flowVersions.value = (versions || []).map((version) => ({
      id: Number(version.id),
      approval_type_id: Number(version.approval_type_id),
      version: Number(version.version || 1),
      status: String(version.status || 'published'),
      published_at: version.published_at,
      snapshot_json: parseRuleJson(version.snapshot_json),
    }))
    selectedFlowVersionId.value = flowVersions.value[0]?.id || null
  } catch {
    flowVersions.value = []
    selectedFlowVersionId.value = null
  }
}

async function saveServerDraft() {
  const snapshot = captureTemplateEditorSnapshot()
  try {
    await put(`/field/template-drafts/${currentTemplate.id || 0}`, snapshot)
    draftTemplateSnapshot.value = snapshot
    ElMessage.success('草稿已保存，已发布版本保持不变')
  } catch { ElMessage.error('草稿保存失败，请重试') }
}

function selectDraftFlowVersion() {
  flowVersionMenuVisible.value = false
  if (!rulesDirty.value && !draftTemplateSnapshot.value) {
    ElMessage.info('当前没有未发布草稿')
    return
  }
  if (draftTemplateSnapshot.value) {
    restoreTemplateEditorSnapshot(draftTemplateSnapshot.value)
  }
  currentFlowVersionMode.value = 'draft'
  selectedFlowVersionId.value = null
}

function applyPublishedVersionSnapshot(version: FlowVersionItem) {
  const snapshot = parseRuleJson(version.snapshot_json)
  const fields = Array.isArray(snapshot.fields) ? snapshot.fields : []
  const flowSnapshot = parseRuleJson(snapshot.flow)
  currentTemplate.id = Number(snapshot.approval_type_id || currentTemplate.id) || currentTemplate.id
  currentTemplate.name = String(snapshot.name || currentTemplate.name)
  const videoCatalogItem = videoTemplateCatalogItem({
    name: currentTemplate.name,
    business_code: snapshot.business_code || currentTemplate.businessCode,
  })
  if (videoCatalogItem) {
    currentTemplate.name = videoCatalogItem.name
  }
  currentTemplate.group = String(snapshot.category || currentTemplate.group || '其他')
  currentTemplate.businessCode = canonicalTemplateBusinessCode({
    name: currentTemplate.name,
    business_code: snapshot.business_code || currentTemplate.businessCode,
  })
  currentTemplate.version = Number(snapshot.version || version.version || currentTemplate.version || 1)
  currentTemplate.status = version.status || 'published'
  currentTemplate.description = String(snapshot.description || currentTemplate.description || '').slice(0, 100)
  editableFields.value = normalizeFieldsForTemplate({
    name: currentTemplate.name,
    business_code: currentTemplate.businessCode,
  }, fields)
  selectedFieldCode.value = preferredSelectedFieldCode(currentTemplate.name, editableFields.value)
  selectedDetailChildCode.value = ''
  detailChildTargetCode.value = ''
  applyRuleSettings(snapshot.permission_rules)
  buildFlowNodesFromServer(Array.isArray(flowSnapshot.nodes) ? flowSnapshot.nodes : [])
  rulesDirty.value = false
}

function selectPublishedFlowVersion(version: FlowVersionItem) {
  flowVersionMenuVisible.value = false
  if (currentFlowVersionMode.value === 'draft' || rulesDirty.value) {
    draftTemplateSnapshot.value = captureTemplateEditorSnapshot()
  }
  applyPublishedVersionSnapshot(version)
  currentFlowVersionMode.value = 'published'
  selectedFlowVersionId.value = version.id
}

function openRuleDialog(dialog: 'visible' | 'submit' | 'templateAdmin') {
  activeRuleDialog.value = dialog
  if (dialog === 'submit') {
    resetRuleMemberPicker(ruleSettings.submitMemberIds)
  }
  if (dialog === 'templateAdmin') {
    resetRuleMemberPicker(ruleSettings.templateAdminIds)
  }
  ruleDialogVisible.value = true
}

function closeRuleDialog() {
  ruleDialogVisible.value = false
  activeRuleDialog.value = ''
}

function confirmRuleDialog() {
  markRulesDirty()
  closeRuleDialog()
}

function composePermissionRules() {
  return {
    visible_scope: {
      type: 'departments',
      department_ids: [...ruleSettings.visibleDepartmentIds],
      include_sub_departments: ruleSettings.includeSubDepartments,
    },
    submit_permission: {
      type: ruleSettings.submitPermissionType,
      roles: [...ruleSettings.submitRoles],
      member_ids: [...ruleSettings.submitMemberIds],
    },
    view_permission: {
      type: ruleSettings.viewPermissionType,
      extra_member_ids: [],
    },
    template_management: {
      type: ruleSettings.templateAdminType === 'selected' ? 'selected_admins' : 'all_approval_admins',
      admin_ids: [...ruleSettings.templateAdminIds],
    },
    field_edit_permissions: {
      fixed_approver: { modifiable: !ruleSettings.fixedApproverLocked },
      fixed_cc: { modifiable: !ruleSettings.fixedCcLocked },
      fixed_handler: { modifiable: !ruleSettings.fixedHandlerLocked },
    },
    in_progress_actions: {
      approval_comment_required: ruleSettings.approvalCommentRequired,
      handler_comment_required: ruleSettings.handlerCommentRequired,
    },
  }
}

function composeExceptionRules() {
  return {}
}

function composeAutoApprovalRule() {
  const rule: Record<string, any> = {}
  if ((currentTemplate.businessCode || templateBusinessCode()) === 'business_trip') {
    rule.attendance_sync = {
      enabled: true,
      duration_mode: 'natural_day',
      status: 'business_trip',
    }
  }
  return rule
}

function templateBusinessCodeForName(name: string) {
  const catalogItem = catalogItemForTemplate({ name })
  if (catalogItem) {
    return catalogItem.business_code
  }
  const knownCodes: Record<string, string> = {
    出差: 'business_trip',
    请假: 'leave',
    外出: 'outside',
    打卡补卡: 'punch_correction',
    补卡: 'punch_correction',
  }
  if (knownCodes[name]) {
    return knownCodes[name]
  }
  if (name === '未命名模板') {
    return `custom_${Date.now()}`
  }
  return name
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/[^\w]/g, '') || 'custom_template'
}

function canonicalTemplateBusinessCode(input: { name?: unknown; business_code?: unknown }) {
  const catalogItem = catalogItemForTemplate(input)
  if (catalogItem?.business_code) {
    return catalogItem.business_code
  }
  const businessCode = String(input.business_code || '').trim()
  if (businessCode) {
    return businessCode
  }
  return templateBusinessCodeForName(String(input.name || ''))
}

function templateBusinessCode() {
  return canonicalTemplateBusinessCode({
    name: currentTemplate.name,
    business_code: currentTemplate.businessCode,
  })
}

function normalizeBusinessTripFieldCode(code: unknown) {
  const normalizedCode = String(code || '').trim()
  if (normalizedCode === 'trip_start_time') return 'start_time'
  if (normalizedCode === 'trip_end_time') return 'end_time'
  return normalizedCode
}

function composeTemplateFields() {
  const businessCode = templateBusinessCode()
  const normalizedFields = editableFields.value.map((field) => ({
    ...field,
    code: businessCode === 'business_trip' ? normalizeBusinessTripFieldCode(field.code) : field.code,
    field_type: normalizeTemplateFieldType(field.field_type, field.code),
    is_required: ['layout_column', 'static_text'].includes(field.field_type) ? false : field.is_required,
    print_visible: field.field_type === 'layout_column' ? false : field.printable !== false,
    is_business_calculation: field.field_type === 'detail' ? detailSummaryEnabled(field) : Boolean(field.is_business_calculation),
    options_json: normalizeTemplateFieldOptions(field.options_json),
  }))
  return normalizedFields
}

function composeOneFlowNode(node: FlowNodeEditor, index: number): Record<string, any> {
  if (isParallelBranchNode(node)) {
    ensureParallelBranches(node)
    return {
      node_order: index + 1,
      node_type: 'parallel_branch',
      approver_type: 'parallel_branch',
      branches: (node.branches || []).map((branch) => ({
        branch_label: branch.label,
        label: branch.label,
        nodes: branch.nodes.map((childNode, childIndex) => composeOneFlowNode(childNode, childIndex)),
      })),
    }
  }
  if (isConditionBranchNode(node)) {
    ensureConditionBranches(node)
    return {
      node_order: index + 1,
      node_type: 'condition_branch',
      approver_type: 'condition_branch',
      branches: (node.branches || []).map((branch) => ({
        branch_label: branch.label,
        label: branch.label,
        is_default_branch: branch.is_default_branch,
        condition_combinator: branch.condition_groups?.[0]?.condition_combinator || branch.condition_combinator,
        condition_group_combinator: branch.condition_group_combinator || 'or',
        conditions: branch.is_default_branch ? [] : (branch.condition_groups?.[0]?.conditions || branch.conditions || []).map((condition) => ({
          field: normalizeConditionField(condition.field),
          operator: condition.operator,
          value: condition.operator === 'empty' || condition.operator === 'not_empty' ? null : condition.value,
        })),
        condition_groups: branch.is_default_branch ? [] : (branch.condition_groups || []).map((group) => ({
          label: group.label || '条件组',
          condition_combinator: group.condition_combinator || 'and',
          conditions: (group.conditions || []).map((condition) => ({
            field: normalizeConditionField(condition.field),
            operator: condition.operator,
            value: condition.operator === 'empty' || condition.operator === 'not_empty' ? null : condition.value,
          })),
        })),
        nodes: branch.nodes.map((childNode, childIndex) => composeOneFlowNode(childNode, childIndex)),
      })),
    }
  }
    const assigneeSource = normalizeAssigneeSource(node.assignee_source)
    const base: Record<string, any> = {
      node_order: index + 1,
      node_type: node.node_type,
      approval_mode: node.approval_mode,
      manager_level: node.manager_level || 1,
      fallback_to_upper_manager: node.fallback_to_upper_manager !== false,
      applicant_self_if_head: Boolean(node.applicant_self_if_head),
      applicant_select_mode: node.applicant_select_mode || 'multiple',
      applicant_select_scope: node.applicant_select_scope || 'company',
      applicant_select_member_ids: normalizeMemberIds(node.applicant_select_member_ids),
      applicant_select_roles: normalizeRoleValues(node.applicant_select_roles),
      multi_level_end_type: node.multi_level_end_type || 'level',
      multi_level_role: node.multi_level_role || node.role || 'manager',
      multi_level_member_ids: normalizeMemberIds(node.multi_level_member_ids),
      multi_level_limit_enabled: Boolean(node.multi_level_limit_enabled),
      multi_level_limit_level: node.multi_level_limit_level || 1,
      multi_level_level: node.multi_level_level || 'highest',
      empty_action: node.empty_action || undefined,
      empty_member_id: node.empty_member_id || undefined,
      vote_pass_count: Number(node.vote_pass_count || 1),
    }
    if (node.node_type === 'notify') {
      const sourceValues = notifySources(node)
      base.allow_applicant_select = node.allow_applicant_select
      base.include_applicant_self = node.include_applicant_self
      base.notify_sources = sourceValues
      base.assignee_source = sourceValues[0] || 'specific_user'
      base.approver_type = base.assignee_source
      if (isNotifySourceSelected(node, 'specific_user')) {
        const memberIds = flowNodeMemberIds(node)
        base.approver_id = memberIds[0] || null
        base.member_ids = memberIds
      }
      if (isNotifySourceSelected(node, 'role')) {
        base.role = node.role
      }
      if (isNotifySourceSelected(node, 'related_member_field')) {
        base.related_member_field_code = node.related_member_field_code
        base.related_field_code = node.related_member_field_code
      }
      if (isNotifySourceSelected(node, 'form_department_head')) {
        base.related_department_field_code = node.related_department_field_code
        base.related_field_code = node.related_department_field_code
      }
      return base
    }
    if (node.node_type === 'auto') {
      base.assignee_source = 'direct_manager'
      base.approver_type = 'auto'
      base.auto_action = node.auto_action || 'approve'
      return base
    }
    if (assigneeSource === 'specific_user') {
      const memberIds = flowNodeMemberIds(node)
      base.assignee_source = 'specific_user'
      base.approver_type = 'specific_user'
      base.approver_id = memberIds[0] || null
      base.member_ids = memberIds
      return base
    }
    if (assigneeSource === 'applicant_self' || assigneeSource === 'applicant_select') {
      base.assignee_source = assigneeSource
      base.approver_type = assigneeSource
      return base
    }
    if (assigneeSource === 'related_member_field') {
      base.assignee_source = 'related_member_field'
      base.approver_type = 'related_member_field'
      base.related_field_code = node.related_member_field_code || node.related_field_code
      base.related_member_field_code = node.related_member_field_code || node.related_field_code
      return base
    }
    if (assigneeSource === 'form_department_head') {
      base.assignee_source = 'form_department_head'
      base.approver_type = 'form_department_head'
      base.related_field_code = node.related_department_field_code || node.related_field_code
      base.related_department_field_code = node.related_department_field_code || node.related_field_code
      return base
    }
    if (assigneeSource === 'role') {
      base.assignee_source = 'role'
      base.approver_type = node.role
      base.role = node.role
      return base
    }
    base.assignee_source = assigneeSource
    base.approver_type = assigneeSource
    return base
}

function composeFlowNodes() {
  normalizeFlowTopologyAfterMutation()
  return flowNodes.value.map((node, index) => composeOneFlowNode(node, index))
}

function currentTemplateSortOrderForSave(categoryName: string) {
  if (currentTemplate.id) {
    const currentItem = allSavedTemplates().find((item) => item.id === currentTemplate.id)
    if (currentItem && Number.isFinite(Number(currentItem.sort_order))) {
      return Number(currentItem.sort_order)
    }
  }
  const group = templateGroups.value.find((item) => item.name === categoryName)
  const savedOrders = (group?.items || [])
    .map((item) => Number(item.sort_order))
    .filter((order) => Number.isFinite(order))
  if (savedOrders.length) {
    return Math.max(...savedOrders) + 10
  }
  const groupOrder = templateCategoryOrder.get(categoryName) ?? 100
  return (groupOrder + 1) * 1000
}

async function saveTemplateConfig() {
  if (!ensureTemplateConfigAccess()) {
    return false
  }
  if (!currentTemplate.name.trim()) {
    ElMessage.error('模板名称不能为空')
    return false
  }
  if (!editableFields.value.length) {
    ElMessage.error('请至少添加一个控件')
    return false
  }
  normalizeFlowTopologyAfterMutation()
  if (!validateConditionBranches(flowNodes.value)) {
    return false
  }
  if (!collectBlockingFlowNodes(flowNodes.value).length) {
    ElMessage.error('请至少配置一个审批、办理或自动处理节点')
    return false
  }
  const executableNodes = collectExecutableFlowNodes(flowNodes.value)
  const missingAssignee = executableNodes.find((node) => hasMissingSpecificMember(node))
  if (missingAssignee) {
    ElMessage.error('存在指定成员但未选择人员的节点，请先完善后再保存')
    return false
  }
  const tooManySpecificMembers = executableNodes.find((node) => (
    (node.assignee_source === 'specific_user' || isNotifySourceSelected(node, 'specific_user'))
    && flowNodeMemberIds(node).length > 50
  ))
  if (tooManySpecificMembers) {
    ElMessage.error('指定成员审批人不能超过50人')
    return false
  }
  const tooManyMultiLevelMembers = executableNodes.find((node) => (
    node.assignee_source === 'multi_level_manager'
    && node.multi_level_end_type === 'member'
    && normalizeMemberIds(node.multi_level_member_ids).length > 50
  ))
  if (tooManyMultiLevelMembers) {
    ElMessage.error('连续多级主管终点指定人员不能超过50人')
    return false
  }
  const missingRole = executableNodes.find(
    (node) => node.node_type !== 'auto' && node.assignee_source === 'role' && !String(node.role || '').trim(),
  )
  if (missingRole) {
    ElMessage.error('存在按角色审批但未选择角色的节点，请先完善后再保存')
    return false
  }
  const missingApplicantSelectMembers = executableNodes.find((node) => (
    node.assignee_source === 'applicant_select'
    && node.applicant_select_scope === 'selected_members'
    && !normalizeMemberIds(node.applicant_select_member_ids).length
  ))
  if (missingApplicantSelectMembers) {
    ElMessage.error('发起人自选范围为指定成员时，请先添加候选成员')
    return false
  }
  const missingApplicantSelectRole = executableNodes.find((node) => (
    node.assignee_source === 'applicant_select'
    && node.applicant_select_scope === 'role'
    && !normalizeRoleValues(node.applicant_select_roles).length
  ))
  if (missingApplicantSelectRole) {
    ElMessage.error('发起人自选范围为角色时，请先添加角色')
    return false
  }
  const missingMultiLevelRole = executableNodes.find((node) => (
    node.assignee_source === 'multi_level_manager'
    && node.multi_level_end_type === 'role'
    && !String(node.multi_level_role || '').trim()
  ))
  if (missingMultiLevelRole) {
    ElMessage.error('连续多级主管终点选择指定角色时，请先选择角色')
    return false
  }
  const missingMultiLevelMembers = executableNodes.find((node) => (
    node.assignee_source === 'multi_level_manager'
    && node.multi_level_end_type === 'member'
    && !normalizeMemberIds(node.multi_level_member_ids).length
  ))
  if (missingMultiLevelMembers) {
    ElMessage.error('连续多级主管终点选择指定人员时，请先在组织架构树中选择人员')
    return false
  }
  const invalidVotePassCount = executableNodes.find((node) => node.approval_mode === 'vote' && Number(node.vote_pass_count || 0) < 1)
  if (invalidVotePassCount) {
    ElMessage.error('投票审批的通过人数必须大于0')
    return false
  }
  const missingRelatedMember = executableNodes.find((node) => (
    (node.assignee_source === 'related_member_field' || isNotifySourceSelected(node, 'related_member_field'))
    && !String(node.related_member_field_code || node.related_field_code || '').trim()
  ))
  if (missingRelatedMember) {
    ElMessage.error('存在表单联系人节点但未选择成员控件，请先完善后再保存')
    return false
  }
  const missingRelatedDepartment = executableNodes.find((node) => (
    (node.assignee_source === 'form_department_head' || isNotifySourceSelected(node, 'form_department_head'))
    && !String(node.related_department_field_code || node.related_field_code || '').trim()
  ))
  if (missingRelatedDepartment) {
    ElMessage.error('存在表单部门主管节点但未选择部门控件，请先完善后再保存')
    return false
  }
  const missingEmptyFallback = executableNodes.find((node) => node.empty_action === 'transfer_to_specific' && !node.empty_member_id)
  if (missingEmptyFallback) {
    ElMessage.error('存在“审批人为为空时指定人员审批”但未选择人员的节点，请先完善后再保存')
    return false
  }
  const unsupportedField = findUnsupportedTemplateField(editableFields.value)
  if (unsupportedField) {
    const fieldName = String(unsupportedField.label || unsupportedField.code || '未命名控件').trim()
    ElMessage.error(`控件“${fieldName}”包含当前不支持保存的类型“${fieldTypeLabel(unsupportedField.field_type)}”`)
    return false
  }
  if (ruleSettings.submitPermissionType === 'selected_members' && !ruleSettings.submitMemberIds.length) {
    ElMessage.error('请选择可以发起该表单的成员')
    return false
  }
  if (ruleSettings.templateAdminType === 'selected' && !ruleSettings.templateAdminIds.length) {
    ElMessage.error('请选择表单管理员')
    return false
  }
  const catalogItem = currentTemplateCatalogItem.value
  const categoryName = currentTemplate.group || catalogItem?.category || '其他'
  const iconKey = catalogItem?.iconKey || 'document'
  const iconTone = catalogItem?.iconTone || catalogItem?.color || templateColorForCategory(categoryName)
  const payload = {
    id: currentTemplate.id,
    name: currentTemplate.name,
    business_code: templateBusinessCode(),
    category: categoryName,
    category_key: templateCategoryKeyForPayload(categoryName, catalogItem),
    scope: 'mobile',
    is_active: currentTemplate.isActive,
    sort_order: currentTemplateSortOrderForSave(categoryName),
    icon: iconKey,
    icon_key: iconKey,
    icon_tone: iconTone,
    description: currentTemplate.description.trim(),
    print_format: { mode: 'default' },
    permission_rules: composePermissionRules(),
    exception_rules: composeExceptionRules(),
    auto_approval_rule: composeAutoApprovalRule(),
    fields: composeTemplateFields(),
    flow_nodes: composeFlowNodes(),
  }
  try {
    const previousDraftId = currentTemplate.id || 0
    const saved: any = await post('/approval/template-configs', payload, {
      timeout: 60000,
      skipNetworkErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    await del(`/field/template-drafts/${previousDraftId}`).catch(() => {})
    currentTemplate.id = saved?.id || currentTemplate.id
    currentTemplate.businessCode = saved?.business_code || currentTemplate.businessCode
    currentTemplate.status = saved?.status || (currentTemplate.isActive ? 'enabled' : 'draft')
    currentTemplate.version = Number(saved?.version || currentTemplate.version || 1)
    currentTemplate.createdAt = saved?.updated_at || saved?.created_at || currentTemplate.createdAt || ''
    rulesDirty.value = false
    currentFlowVersionMode.value = 'published'
    draftTemplateSnapshot.value = null
    await loadTemplateGroups()
    if (currentTemplate.id) {
      await hydrateTemplateFromApi(currentTemplate.id)
    }
    ElMessage.success('模板和审批流程已保存')
    return true
  } catch (error: any) {
    if (error?.code === 'ECONNABORTED') {
      ElMessage.error('模板保存超时，请稍后重试')
      return false
    }
    if (error?.code === 'ERR_NETWORK') {
      ElMessage.error('模板保存失败，请确认后端服务已启动后再试')
      return false
    }
    ElMessage.error(error?.response?.data?.detail || '模板保存失败，请稍后重试')
    return false
  }
}

function stayOnRules() {
  dirtyRuleDialogVisible.value = false
  pendingSettingsTab.value = null
  pendingEditorStep.value = null
}

async function saveRulesAndContinue() {
  const saved = await saveTemplateConfig()
  if (!saved) return
  dirtyRuleDialogVisible.value = false
  if (pendingSettingsTab.value) {
    settingsTab.value = pendingSettingsTab.value
    if (pendingEditorStep.value) {
      editorStep.value = pendingEditorStep.value
      settingsTab.value = 'template'
    } else if (pendingSettingsTab.value === 'template') {
      editorStep.value = 'basic'
    }
    if (pendingSettingsTab.value === 'records') {
      loadCurrentTemplateRecords()
    }
  }
  pendingSettingsTab.value = null
  pendingEditorStep.value = null
}

function approvalArchiveBusinessType(contextBusinessType = archiveContextBusinessType.value) {
  return contextBusinessType || archiveFilters.business_type || undefined
}

async function loadApprovalArchive(contextBusinessType = archiveContextBusinessType.value) {
  if (!canManageTemplateConfigs.value) return
  archiveContextBusinessType.value = contextBusinessType || null
  approvalArchiveLoading.value = true
  try {
    const result: any = await get('/approval/instances', {
      group: 'submitted',
      skip: 0,
      limit: 100,
      business_type: approvalArchiveBusinessType(contextBusinessType),
      status: archiveFilters.status || undefined,
      start_date: archiveFilters.start_date || undefined,
      end_date: archiveFilters.end_date || undefined,
      applicant_id: archiveFilters.applicant_id || undefined,
    }, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    approvalArchiveRows.value = Array.isArray(result?.items) ? result.items : []
    approvalArchiveTotal.value = Number(result?.total || approvalArchiveRows.value.length || 0)
  } catch (error: any) {
    approvalArchiveRows.value = []
    approvalArchiveTotal.value = 0
    if (error?.response?.status !== 403) {
      ElMessage.error(error?.response?.data?.detail || '审批记录加载失败')
    }
  } finally {
    approvalArchiveLoading.value = false
  }
}

function loadOverviewArchive() {
  archiveContextBusinessType.value = null
  void loadApprovalArchive(null)
}

function loadCurrentTemplateRecords() {
  void loadApprovalArchive(currentTemplate.businessCode || null)
}

function switchOverviewSection(section: OverviewSection) {
  if (section === 'data' && !canManageTemplateConfigs.value) {
    activeOverviewSection.value = 'forms'
    return
  }
  activeOverviewSection.value = section
  if (section === 'data') {
    loadOverviewArchive()
    return
  }
  void nextTick(updateFormManagerStickyHeight)
}

function resetApprovalArchiveFilters() {
  archiveFilters.start_date = ''
  archiveFilters.end_date = ''
  archiveFilters.business_type = ''
  archiveFilters.status = ''
  archiveFilters.applicant_id = null
  archiveFilters.applicant_keyword = ''
  if (settingsTab.value === 'records' && screen.value === 'settings') {
    loadCurrentTemplateRecords()
    return
  }
  loadOverviewArchive()
}

function formatApprovalDateTime(value?: string) {
  if (!value) return '-'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value).replace('T', ' ').slice(0, 16)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed).replace(/\//g, '-')
}

function approvalRecordNo(record: ApprovalArchiveRow) {
  const dateText = String(record.created_at || '').slice(0, 10).replaceAll('-', '')
  return `${dateText || 'APP'}${String(record.id || '').padStart(4, '0')}`
}

function approvalRecordType(record: ApprovalArchiveRow) {
  const raw = String(record.approval_type_name || record.business_type || record.module || '').trim()
  const match = approvalTemplateCatalog.find((item) => item.business_code === raw || item.codeAliases?.includes(raw))
  return match?.name || raw || '审批'
}

function approvalRecordApplicant(record: ApprovalArchiveRow) {
  return record.applicant_name || (record.applicant_id ? `员工 #${record.applicant_id}` : '-')
}

function approvalRecordFinish(record: ApprovalArchiveRow) {
  const status = String(record.status || '').toLowerCase()
  if (status === 'pending') return '-'
  return formatApprovalDateTime(record.updated_at)
}

function approvalRecordNode(record: ApprovalArchiveRow) {
  const status = String(record.status || '').toLowerCase()
  if (status !== 'pending') return record.current_node_status_label || approvalStatusText(record.status)
  const nodeName = record.current_node_name || (record.current_node_order ? `第 ${record.current_node_order} 节点` : '审批节点')
  return `${nodeName} · ${record.current_node_status_label || '待审批'}`
}

function approvalStatusText(status?: string) {
  const map: Record<string, string> = {
    pending: '待审批',
    approved: '已通过',
    rejected: '已拒绝',
    withdrawn: '已撤回',
    cancelled: '已取消',
  }
  return map[String(status || '').toLowerCase()] || status || '-'
}

function openApprovalRecord(record: ApprovalArchiveRow) {
  ElMessageBox.alert(
    [
      `审批编号：${approvalRecordNo(record)}`,
      `审批类型：${approvalRecordType(record)}`,
      `申请人：${approvalRecordApplicant(record)}`,
      `提交时间：${formatApprovalDateTime(record.created_at)}`,
      `当前节点：${approvalRecordNode(record)}`,
      `审批状态：${approvalStatusText(record.status)}`,
      record.summary ? `申请内容：${record.summary}` : '',
    ].filter(Boolean).join('\n'),
    '审批详情',
    { confirmButtonText: '关闭' },
  )
}

function csvCell(value: unknown) {
  return `"${String(value ?? '').replace(/"/g, '""')}"`
}

function downloadTextFile(filename: string, content: string, mime = 'text/csv;charset=utf-8;') {
  const blob = new Blob(['\uFEFF' + content], { type: mime })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function exportCurrentTemplateRecords() {
  if (!records.value.length) {
    ElMessage.info('当前筛选条件下没有可导出的审批记录')
    return
  }
  const rows = records.value.map((record) => [
    approvalRecordNo(record),
    approvalRecordType(record),
    formatApprovalDateTime(record.created_at),
    approvalRecordFinish(record),
    approvalRecordApplicant(record),
    approvalRecordNode(record),
    approvalStatusText(record.status),
    record.summary || '',
  ])
  const csv = [
    ['审批编号', '审批类型', '提交时间', '完成时间', '申请人', '当前节点', '审批状态', '摘要'],
    ...rows,
  ].map((row) => row.map(csvCell).join(',')).join('\n')
  downloadTextFile(`审批记录_${new Date().toISOString().slice(0, 10)}.csv`, csv)
  ElMessage.success('审批记录已导出')
}

function collectAttachmentUrls(value: unknown, out: Set<string>) {
  if (!value) return
  if (typeof value === 'string') {
    if (/^https?:\/\//i.test(value) || /^\/?uploads\//i.test(value)) out.add(value)
    return
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectAttachmentUrls(item, out))
    return
  }
  if (typeof value === 'object') {
    Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
      const normalizedKey = key.toLowerCase()
      if (/(attachment|proof|file|image|url|path)/.test(normalizedKey)) {
        collectAttachmentUrls(item, out)
      } else if (typeof item === 'object') {
        collectAttachmentUrls(item, out)
      }
    })
  }
}

async function exportCurrentTemplateAttachmentManifest() {
  if (!records.value.length) {
    ElMessage.info('当前筛选条件下没有可导出的审批记录')
    return
  }
  const manifestRows: string[][] = []
  for (const record of records.value) {
    try {
      const detail: any = await get(`/approval/instances/${record.id}`, undefined, {
        skipErrorReport: true,
        skipDefaultErrorHandler: true,
      })
      const urls = new Set<string>()
      collectAttachmentUrls(detail?.form_data, urls)
      collectAttachmentUrls(detail?.attachments, urls)
      urls.forEach((url) => {
        manifestRows.push([
          approvalRecordNo(record),
          approvalRecordType(record),
          approvalRecordApplicant(record),
          url,
        ])
      })
    } catch {
      // 单条详情读取失败不阻断其他审批记录的附件清单导出。
    }
  }
  if (!manifestRows.length) {
    ElMessage.info('当前审批记录没有可导出的附件链接')
    return
  }
  const csv = [
    ['审批编号', '审批类型', '申请人', '附件链接'],
    ...manifestRows,
  ].map((row) => row.map(csvCell).join(',')).join('\n')
  downloadTextFile(`审批附件清单_${new Date().toISOString().slice(0, 10)}.csv`, csv)
  ElMessage.success('附件清单已导出')
}

function hasUsableToken() {
  return hasUsableWebAuthToken()
}

async function loadTemplateGroups() {
  try {
    const groups: any[] = await get('/approval/templates', canManageTemplateConfigs.value ? { include_inactive: true } : undefined, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    setTemplateGroupsFromItems(mergeAllowedTemplateItems(groups || []))
  } catch {
    setTemplateGroupsFromItems(mergeAllowedTemplateItems([]))
  }
}

async function toggleTemplateStatus(item: TemplateMenuItem, isActive: boolean) {
  if (!ensureTemplateConfigAccess()) return
  if (!item.id) return
  const previousStatus = item.is_active !== false
  if (previousStatus === isActive) return
  templateStatusUpdatingId.value = item.id
  try {
    await put(`/approval/types/${item.id}`, {
      is_active: isActive,
      status: isActive ? 'enabled' : 'disabled',
    }, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    if (currentTemplate.id === item.id) {
      currentTemplate.isActive = isActive
      currentTemplate.status = isActive ? 'enabled' : 'disabled'
    }
    ElMessage.success(isActive ? '模板已启用' : '模板已停用')
    await loadTemplateGroups()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '模板状态更新失败，请稍后重试')
    await loadTemplateGroups()
  } finally {
    templateStatusUpdatingId.value = null
  }
}

async function deleteTemplate(item: TemplateMenuItem) {
  if (!ensureTemplateConfigAccess()) return
  if (!item.id) return
  try {
    await ElMessageBox.confirm(`确定删除模板「${item.name}」吗？删除后不可恢复。`, '删除模板', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
    await del(`/approval/types/${item.id}`, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    ElMessage.success('模板已删除')
    if (currentTemplate.id === item.id) {
      screen.value = 'overview'
      currentTemplate.id = null
    }
    await loadTemplateGroups()
  } catch (error: any) {
    if (error === 'cancel' || error === 'close' || error?.action === 'cancel' || error?.action === 'close') {
      return
    }
    if (error?.response?.status === 409) {
      ElMessage.error('模板已被历史审批记录引用，无法删除，请先停用模板')
      return
    }
    ElMessage.error(error?.response?.data?.detail || '删除模板失败，请稍后重试')
  }
}

async function loadPeopleAndDepartments() {
  if (!hasUsableToken()) {
    memberOptions.value = []
    departmentOptions.value = []
    return
  }

  try {
    const members = await get('/employees/selector', { limit: 300 }, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    memberOptions.value = (Array.isArray(members) ? members : []).map((member: any) => ({
      id: Number(member.id),
      name: String(member.name || `员工#${member.id}`),
      employee_no: member.employee_no ? String(member.employee_no) : '',
      department_id: member.department_id ?? null,
      department_name: member.department_name ? String(member.department_name) : '',
      role_names: normalizeRoleValues(member.role_names || member.roles),
    }))

    if (!hasUsableToken()) {
      departmentOptions.value = []
      return
    }

    const departments = await get('/departments', undefined, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    const departmentList = (departments?.items || departments?.data || departments || []) as any[]
    departmentOptions.value = departmentList.map((dept) => ({
      id: Number(dept.id),
      name: String(dept.name || `部门#${dept.id}`),
      parent_id: dept.parent_id ?? null,
    }))

    const companies = await get('/payroll/companies', { active_only: false }, {
      skipErrorReport: true,
      skipDefaultErrorHandler: true,
    })
    companyOptions.value = (Array.isArray(companies) ? companies : []).map((company: any) => ({
      id: Number(company.id),
      name: String(company.name || `公司#${company.id}`),
      short_name: company.short_name ? String(company.short_name) : '',
      parent_company_id: company.parent_company_id ?? null,
    }))
  } catch {
    memberOptions.value = []
    departmentOptions.value = []
    companyOptions.value = []
  }
}

onMounted(async () => {
  normalizeRuleSettings()
  resetEditableFields(currentTemplate.name)
  flowNodes.value = defaultFlowNodes()
  selectedFlowNodeUid.value = flowNodes.value[0]?.uid || ''
  if (!hasUsableToken()) {
    clearWebAuthSession()
    templateGroups.value = []
    memberOptions.value = []
    departmentOptions.value = []
    companyOptions.value = []
    await router.replace('/login')
    return
  }
  await Promise.all([loadTemplateGroups(), loadPeopleAndDepartments()])
  if (canManageTemplateConfigs.value) {
    await loadApprovalArchive(null)
  }
  await nextTick()
  updateFormManagerStickyHeight()
  if (typeof ResizeObserver !== 'undefined' && formManagerStickyRef.value) {
    formManagerResizeObserver = new ResizeObserver(updateFormManagerStickyHeight)
    formManagerResizeObserver.observe(formManagerStickyRef.value)
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', updateFormManagerStickyHeight)
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('click', handleTemplateDocumentClick)
  }
})

onBeforeUnmount(() => {
  formManagerResizeObserver?.disconnect()
  formManagerResizeObserver = null
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', updateFormManagerStickyHeight)
  }
  if (typeof document !== 'undefined') {
    document.removeEventListener('click', handleTemplateDocumentClick)
  }
})

watch([templateBatchMode, canManageTemplateConfigs, templateSearchKeyword], () => {
  void nextTick(updateFormManagerStickyHeight)
})

watch(departmentKeyword, (keyword) => {
  if (departmentTreeRef.value?.filter) {
    departmentTreeRef.value.filter(keyword)
  }
})

watch(applicantScopeKeyword, (keyword) => {
  if (applicantScopeMode.value === 'org' && applicantScopeTreeRef.value?.filter) {
    applicantScopeTreeRef.value.filter(keyword)
  }
})

watch(memberKeyword, (keyword) => {
  if (memberPickerPurpose.value === 'multi_level_member' && memberTreeRef.value?.filter) {
    memberTreeRef.value.filter(keyword)
  }
})

watch(archiveApplicantDepartmentKeyword, (keyword) => {
  if (archiveDepartmentTreeRef.value?.filter) {
    archiveDepartmentTreeRef.value.filter(keyword)
  }
})

watch(ruleDepartmentKeyword, (keyword) => {
  if (ruleDepartmentTreeRef.value?.filter) {
    ruleDepartmentTreeRef.value.filter(keyword)
  }
})
</script>

<style scoped>
.wecom-approval-page {
  min-height: calc(100vh - 108px);
  color: #1f2937;
  font-size: 16px;
}

button, input, select {
  font: inherit;
}

button {
  cursor: pointer;
}

.wecom-main {
  min-width: 0;
}

.wecom-card {
  min-height: calc(100vh - 108px);
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

.settings-page {
  overflow: hidden;
  border-radius: 0;
}

.detail-topbar, .settings-header {
  height: 64px;
  border-bottom: 1px solid #edf0f4;
  display: grid;
  grid-template-columns: 64px 1fr 64px;
  align-items: center;
  text-align: center;
  font-weight: 700;
}

.back-btn {
  width: 34px;
  height: 34px;
  margin-left: 16px;
  border: 1px solid #dce3eb;
  border-radius: 6px;
  background: #fff;
  color: #5b6472;
  font-size: 28px;
  line-height: 28px;
}

.app-detail {
  padding-bottom: 32px;
}

.app-hero {
  display: flex;
  align-items: center;
  gap: 26px;
  margin: 32px 42px 0;
  padding-bottom: 40px;
  border-bottom: 1px solid #edf0f4;
}

.approval-icon {
  width: 92px;
  height: 92px;
  border-radius: 10px;
  background: #ffb51d;
  position: relative;
  flex: 0 0 auto;
}

.approval-icon::before {
  content: '';
  position: absolute;
  left: 35px;
  top: 18px;
  width: 22px;
  height: 46px;
  border-radius: 14px 14px 4px 4px;
  background: #fff;
}

.approval-icon::after {
  content: '';
  position: absolute;
  left: 24px;
  bottom: 20px;
  width: 44px;
  height: 8px;
  background: #fff;
}

.app-hero h1 {
  margin: 0 0 8px;
  font-size: 28px;
}

.app-hero p {
  margin: 0;
  color: #6b7280;
  line-height: 1.7;
}

.detail-row {
  min-height: 86px;
  display: flex;
  align-items: center;
  gap: 26px;
  margin: 0 42px;
  border-bottom: 1px solid #edf0f4;
}

.row-label {
  width: 150px;
  font-weight: 600;
}

.dept-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 36px;
  padding: 0 12px;
  border: 1px solid #e3e7ed;
  border-radius: 5px;
  background: #fff;
}

.folder-icon {
  width: 19px;
  height: 14px;
  border-radius: 3px;
  background: #8dbbf2;
  display: inline-block;
}

.link-btn, .plain-link {
  border: 0;
  background: transparent;
  color: #1e63a9;
  padding: 0;
}

.danger-link {
  color: #d03050;
}

.muted {
  color: #8b95a1;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin: 40px 42px 0;
}

.feature-card {
  min-height: 190px;
  border: 1px solid #e0e5ec;
  border-radius: 4px;
  padding: 26px 34px;
}

.feature-card h3 {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 10px;
  font-size: 20px;
}

.feature-card p {
  color: #737f8d;
  line-height: 1.65;
}

.feature-links {
  display: flex;
  gap: 22px;
  margin-top: 48px;
}

.thin-icon {
  width: 20px;
  height: 20px;
  border: 2px solid #333;
  border-radius: 3px;
}

.handover-count b {
  color: #d15b2d;
}

.template-manager {
  margin-top: 24px;
  padding: 36px 44px;
}

.template-manager h2 {
  margin: 0 0 28px;
  font-size: 20px;
}

.primary-btn, .secondary-btn {
  min-width: 126px;
  height: 46px;
  border-radius: 4px;
  padding: 0 20px;
}

.primary-btn {
  border: 1px solid #2f7de1;
  background: #2f7de1;
  color: #fff;
}

.secondary-btn {
  border: 1px solid #d9dfe7;
  background: #fff;
  color: #202936;
}

.add-template-btn {
  margin-bottom: 16px;
  font-size: 18px;
}

.template-section {
  border: 1px solid #e0e5ec;
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 18px;
}

.section-title {
  height: 50px;
  display: flex;
  align-items: center;
  padding: 0 28px;
  background: #f6f7f9;
  color: #626b78;
}

.template-row {
  min-height: 60px;
  display: grid;
  grid-template-columns: minmax(240px, 1fr) minmax(320px, 1fr) 170px;
  align-items: center;
  gap: 24px;
  padding: 0 28px;
  cursor: pointer;
}

.template-row--readonly {
  cursor: default;
}

.template-row + .template-row {
  border-top: 1px solid #eef1f4;
}

.template-row.focused {
  background: #f4f4f4;
}

.template-name {
  display: flex;
  align-items: center;
  gap: 14px;
}

.template-name strong {
  font-size: 18px;
  font-weight: 500;
}

.template-name small {
  display: block;
  color: #8b95a1;
  margin-top: 2px;
}

.template-icon {
  width: 34px;
  height: 34px;
  border-radius: 4px;
  background: #14b8c6;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.template-icon :deep(svg) {
  width: 20px;
  height: 20px;
}

.template-icon.yellow { background: #f8b81e; }
.template-icon.blue { background: #1eb0e8; }
.template-icon.teal { background: #10bfa9; }
.template-icon.green { background: #00a58a; }
.template-icon.orange { background: #f0a800; }
.template-icon.gold { background: #c79747; }

.disabled-tag {
  border: 1px solid #cfd4db;
  color: #9aa3ad;
  border-radius: 2px;
  font-size: 13px;
  padding: 2px 7px;
}

.template-scope {
  color: #777f89;
  display: flex;
  gap: 12px;
  align-items: center;
}

.template-status-switch {
  display: inline-flex;
  align-items: center;
}

.template-actions {
  display: flex;
  justify-content: flex-end;
  gap: 22px;
}

.approval-overview-tabs {
  display: flex;
  align-items: center;
  gap: 38px;
  margin: -4px 0 18px;
  padding: 0 8px;
  border-bottom: 1px solid #e6ebf2;
}

.approval-overview-tabs button {
  position: relative;
  height: 48px;
  border: 0;
  background: transparent;
  padding: 0 2px;
  color: #747d8b;
  font-size: 18px;
  font-weight: 600;
}

.approval-overview-tabs button.active {
  color: #111827;
}

.approval-overview-tabs button.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 3px;
  border-radius: 999px;
  background: #111827;
}

.approval-form-manager {
  min-height: calc(100vh - 108px);
  padding: 30px 32px 40px;
  overflow: visible;
  position: relative;
}

.form-manager-sticky {
  position: sticky;
  top: -24px;
  z-index: 20;
  isolation: isolate;
  margin: -30px -32px 0;
  padding: 30px 32px 20px;
  border-radius: 8px 8px 0 0;
  background: #fff;
  box-shadow: 0 10px 18px rgba(15, 23, 42, 0.035);
}

.form-manager-sticky::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: -120px;
  height: 120px;
  background: #fff;
  pointer-events: none;
  z-index: -1;
}

.form-manager-header {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(0, auto);
  align-items: center;
  gap: 24px;
}

.form-manager-header h1 {
  margin: 0;
  color: #111827;
  font-size: 24px;
  line-height: 32px;
  font-weight: 700;
}

.form-manager-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  min-width: 0;
}

.form-search {
  width: min(320px, 28vw);
  min-width: 220px;
  height: 46px;
  border-radius: 999px;
  background: #f0f2f6;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 18px;
}

.form-search svg {
  width: 20px;
  height: 20px;
  color: #9aa3ad;
  flex: 0 0 auto;
}

.form-search input {
  min-width: 0;
  width: 100%;
  height: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: #1f2937;
}

.form-search input::placeholder {
  color: #9aa3ad;
}

.secondary-pill-btn,
.primary-split-btn,
.group-sort-btn,
.template-group-actions button {
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  white-space: nowrap;
}

.secondary-pill-btn {
  height: 46px;
  border: 1px solid #dfe4ec;
  background: #fff;
  color: #1f2937;
  padding: 0 18px;
}

.secondary-pill-btn.active {
  border-color: #0f6dff;
  color: #0f6dff;
  background: #eff6ff;
}

.secondary-pill-btn:disabled {
  color: #a8b0bb;
  background: #f8fafc;
  cursor: not-allowed;
}

.primary-split-btn {
  height: 46px;
  border: 1px solid #0f6dff;
  background: #0f6dff;
  color: #fff;
  padding: 0 14px 0 18px;
  box-shadow: 0 8px 18px rgba(15, 109, 255, 0.18);
}

.primary-split-btn.add-template-btn {
  margin-bottom: 0;
  font-size: 16px;
}

.primary-split-btn i {
  width: 1px;
  height: 22px;
  background: rgba(255, 255, 255, 0.42);
}

.secondary-pill-btn svg,
.primary-split-btn svg,
.group-sort-btn svg,
.template-group-actions svg,
.template-table-actions svg {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
}

.template-category-bar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 20px;
  margin-top: 28px;
  padding-bottom: 18px;
  border-bottom: 1px solid #eef1f5;
}

.template-category-tabs {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 30px;
  overflow-x: auto;
}

.template-category-tabs button {
  position: relative;
  height: 42px;
  border: 0;
  background: transparent;
  color: #6b7280;
  padding: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  white-space: nowrap;
}

.template-category-tabs button[aria-selected='true'] {
  color: #111827;
}

.template-category-tabs button[aria-selected='true']::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -18px;
  height: 3px;
  border-radius: 999px;
  background: #111827;
}

.group-sort-btn {
  height: 38px;
  border: 0;
  border-left: 1px solid #e5e9ef;
  border-radius: 0;
  background: #fff;
  color: #6b7280;
  padding: 0 0 0 22px;
}

.approval-form-manager .readonly-tip {
  margin: 18px 0 0;
}

.template-group-panel {
  margin-top: 22px;
  position: relative;
  scroll-margin-top: calc(var(--form-manager-sticky-height, 170px) + 22px);
}

.template-group-panel--menu-open {
  z-index: 40;
}

.template-group-header {
  min-height: 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.template-group-title {
  min-width: 0;
  border: 0;
  background: transparent;
  color: #1f2937;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  text-align: left;
}

.template-group-title svg {
  width: 18px;
  height: 18px;
  color: #8b95a1;
  flex: 0 0 auto;
}

.template-group-title strong {
  font-size: 18px;
}

.template-group-title span {
  color: #7a8491;
}

.template-group-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 18px;
}

.template-group-actions button {
  min-height: 34px;
  border: 0;
  background: transparent;
  color: #6b7280;
  padding: 0;
}

.template-table-shell {
  margin-top: 10px;
  border: 1px solid #e2e7ee;
  border-radius: 7px;
  overflow: hidden;
}

.template-table-shell.template-table-shell--menu-open {
  overflow: visible;
}

.template-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.template-table th {
  height: 48px;
  background: #f1f2f4;
  color: #6b7280;
  font-weight: 500;
  text-align: left;
}

.template-table th,
.template-table td {
  padding: 0 20px;
  border-bottom: 1px solid #e9edf2;
  vertical-align: middle;
}

.template-table th:nth-child(1),
.template-table td:nth-child(1) {
  width: 32%;
}

.template-table th:nth-child(2),
.template-table td:nth-child(2) {
  width: 24%;
}

.template-table th:nth-child(3),
.template-table td:nth-child(3) {
  width: 27%;
}

.template-table th:nth-child(4),
.template-table td:nth-child(4) {
  width: 17%;
}

.template-table tbody tr {
  height: 64px;
  background: #fff;
  transition: background 0.16s ease;
}

.template-table tbody tr:hover {
  background: #f8fbff;
}

.template-table tbody tr:last-child td {
  border-bottom: 0;
}

.template-table tbody tr.template-row--menu-open {
  position: relative;
  z-index: 2;
}

.template-table tbody tr.template-row--menu-open td:last-child {
  position: relative;
  z-index: 3;
}

.template-empty-row {
  height: 76px;
  color: #8b95a1;
  text-align: center;
}

.template-table .template-name {
  min-width: 0;
}

.template-title {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.template-title-line,
.template-scope-cell {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.template-title-line strong,
.template-scope-cell > span {
  min-width: 0;
}

.row-mini-action {
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 50%;
  background: #eef5ff;
  color: #0f6dff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.16s ease, background 0.16s ease;
}

.row-mini-action svg {
  width: 13px;
  height: 13px;
}

.template-table tbody tr:hover .row-mini-action,
.row-mini-action:focus-visible {
  opacity: 1;
}

.row-mini-action:hover {
  background: #dcecff;
}

.template-inline-state {
  min-height: 22px;
  border-radius: 999px;
  padding: 0 8px;
  display: inline-flex;
  align-items: center;
  background: #eef1f5;
  color: #6b7280;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.template-inline-state--draft {
  background: #fff7db;
  color: #a16207;
}

.template-table .template-name strong {
  min-width: 0;
  overflow: hidden;
  color: #1f2937;
  font-size: 16px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-check {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.template-check input {
  width: 17px;
  height: 17px;
  padding: 0;
  accent-color: #0f6dff;
}

.template-state {
  min-height: 26px;
  border-radius: 999px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.template-state--active {
  color: #15803d;
  background: #edfdf3;
}

.template-state--draft {
  color: #a16207;
  background: #fff7db;
}

.template-state--disabled {
  color: #6b7280;
  background: #eef1f5;
}

.template-table-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.template-table-actions .plain-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #0f6dff;
  white-space: nowrap;
}

.template-table-actions .plain-link svg {
  color: currentColor;
}

.template-more-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.template-more-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 12px);
  z-index: 35;
  width: 124px;
  padding: 8px;
  display: grid;
  gap: 2px;
  border: 1px solid #dfe5ee;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 16px 42px rgba(15, 23, 42, 0.18);
}

.template-more-menu button {
  height: 36px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #1f2937;
  padding: 0 12px;
  text-align: left;
  font-size: 15px;
  cursor: pointer;
}

.template-more-menu button:hover,
.template-more-menu button:focus-visible {
  background: #eef5ff;
  color: #0f6dff;
}

.template-more-menu button:disabled {
  color: #b6beca;
  cursor: not-allowed;
  background: transparent;
}

.template-more-pop-enter-active,
.template-more-pop-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}

.template-more-pop-enter-from,
.template-more-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.template-icon.purple { background: #7c3aed; }
.template-icon.cyan { background: #14b8c6; }

.settings-tabs {
  min-height: 82px;
  display: flex;
  align-items: flex-end;
  gap: 36px;
  padding: 0 48px;
  border-bottom: 1px solid #e9edf2;
  flex-wrap: wrap;
}

.settings-tabs button {
  height: 54px;
  border: 0;
  background: transparent;
  font-weight: 700;
  border-bottom: 3px solid transparent;
}

.settings-tabs button[aria-selected='true'] {
  color: #111827;
  border-bottom-color: #2f7de1;
}

.approval-editor-topbar {
  height: 78px;
  display: grid;
  grid-template-columns: minmax(280px, 1fr) auto minmax(280px, 1fr);
  align-items: center;
  gap: 24px;
  padding: 0 28px;
  background: #fff;
  border-bottom: 1px solid #e5e8ed;
}

.approval-editor-title {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}

.editor-back-btn {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #111827;
  font-size: 30px;
  line-height: 28px;
}

.editor-template-icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
}

.editor-template-icon :deep(svg) {
  width: 22px;
  height: 22px;
}

.editor-title-text {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.editor-title-text strong {
  color: #111827;
  font-size: 18px;
  line-height: 22px;
}

.editor-title-text small {
  color: #687280;
  font-size: 13px;
}

.editor-title-caret {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  display: grid;
  place-items: center;
}

.editor-title-caret svg {
  width: 16px;
  height: 16px;
}

.approval-editor-steps {
  min-width: max-content;
  align-self: stretch;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 34px;
}

.approval-editor-steps button {
  position: relative;
  height: 100%;
  border: 0;
  border-bottom: 3px solid transparent;
  background: transparent;
  color: #111827;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: 18px;
  font-weight: 700;
}

.approval-editor-steps button > span {
  width: 30px;
  height: 30px;
  border: 2px solid currentColor;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 17px;
  line-height: 1;
}

.approval-editor-steps button[aria-selected='true'] {
  color: #168dff;
  border-bottom-color: #168dff;
}

.approval-editor-actions {
  justify-self: end;
  display: flex;
  align-items: center;
  gap: 12px;
}

.approval-editor-actions button {
  height: 42px;
  border-radius: 6px;
  font-weight: 700;
}

.editor-help-btn {
  border: 0;
  background: transparent;
  color: #666f7c;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.editor-help-btn span {
  width: 18px;
  height: 18px;
  border: 1px solid currentColor;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 12px;
}

.editor-preview-btn {
  min-width: 72px;
  border: 1px solid #d9dee7;
  background: #fff;
  color: #111827;
}

.editor-publish-btn {
  min-width: 86px;
  border: 0;
  background: #168dff;
  color: #fff;
  box-shadow: 0 6px 14px rgba(22, 141, 255, 0.22);
}

.sync-note {
  margin-left: auto;
  color: #9aa3ad;
  align-self: center;
}

.approval-step-panel {
  padding: 28px 56px 60px;
}

.basic-settings {
  display: flex;
  justify-content: center;
  background: #f5f6f8;
}

.basic-card {
  width: min(100%, 760px);
  min-height: calc(100vh - 280px);
  background: #fff;
  border-radius: 8px;
  padding: 34px 42px 42px;
}

.flow-designer-toolbar h2 {
  margin: 0;
  font-size: 22px;
}

.basic-identity-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.basic-icon-picker {
  position: relative;
  width: 64px;
  height: 64px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.basic-icon-picker .template-icon {
  width: 64px;
  height: 64px;
  border-radius: 8px;
}

.basic-icon-edit {
  position: absolute;
  right: -1px;
  bottom: -1px;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 4px;
  background: #56616d;
  color: #fff;
}

.basic-icon-edit svg {
  width: 15px;
  height: 15px;
}

.basic-field,
.basic-radio-block {
  display: grid;
  gap: 10px;
  margin: 0 0 26px;
}

.basic-field--textarea {
  margin-bottom: 42px;
}

.basic-field > span:first-child,
.basic-radio-block legend {
  color: #1f2937;
  font-size: 16px;
  font-weight: 700;
}

.basic-field b,
.basic-radio-block b {
  color: #ff4d4f;
}

.basic-input-wrap,
.basic-textarea-wrap {
  position: relative;
  display: flex;
  min-width: 0;
}

.basic-field input,
.basic-field select,
.basic-field textarea {
  width: 100%;
  border: 1px solid #d9dee7;
  border-radius: 5px;
  background: #fff;
  color: #1f2937;
  font: inherit;
}

.basic-field input,
.basic-field select {
  min-height: 42px;
  padding: 0 62px 0 12px;
}

.basic-field select {
  padding-right: 34px;
}

.basic-field textarea {
  min-height: 100px;
  resize: vertical;
  padding: 12px 12px 28px;
}

.basic-input-wrap small,
.basic-textarea-wrap small {
  position: absolute;
  right: 12px;
  color: #8b95a1;
  pointer-events: none;
}

.basic-input-wrap small {
  top: 50%;
  transform: translateY(-50%);
}

.basic-textarea-wrap small {
  right: 0;
  bottom: -26px;
  font-size: 16px;
}

.basic-radio-block {
  border: 0;
  padding: 0;
}

.basic-radio-line {
  display: flex;
  flex-wrap: wrap;
  gap: 18px 24px;
  align-items: center;
}

.basic-scope-block {
  margin-bottom: 26px;
}

.basic-scope-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 18px;
}

.basic-radio-line label,
.basic-status-toggle {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  color: #1f2937;
  font-size: 15px;
}

.basic-select-link {
  max-width: 100%;
  border: 0;
  background: transparent;
  color: #0f7dff;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.basic-help {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  margin-left: 6px;
  border: 1px solid #aeb6c2;
  border-radius: 50%;
  color: #8b95a1;
  font-size: 12px;
  font-weight: 700;
}

.basic-status-toggle {
  margin-top: 4px;
}

.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 26px;
}

.form-designer-shell {
  min-height: calc(100vh - 186px);
  display: grid;
  grid-template-columns: 456px minmax(520px, 1fr) 432px;
  background: #f4f5f7;
  border-top: 1px solid #edf0f4;
}

.control-workbench {
  min-width: 0;
  display: grid;
  grid-template-columns: 66px minmax(0, 1fr);
  background: #fff;
  border-right: 1px solid #e5e9ef;
}

.control-rail {
  border-right: 1px solid #edf0f4;
  display: grid;
  align-content: start;
  gap: 6px;
  padding: 22px 0;
}

.control-rail button {
  min-height: 62px;
  border: 0;
  border-left: 3px solid transparent;
  background: transparent;
  color: #1f2937;
  display: grid;
  place-items: center;
  gap: 4px;
  font-size: 12px;
}

.control-rail button[aria-selected='true'] {
  border-left-color: #0f7dff;
  color: #0f7dff;
  font-weight: 700;
}

.control-rail svg {
  width: 21px;
  height: 21px;
}

.control-palette {
  min-width: 0;
  max-height: calc(100vh - 186px);
  overflow-y: auto;
  padding: 0 14px 26px;
}

.control-palette h2 {
  position: sticky;
  top: 0;
  z-index: 2;
  margin: 0 -14px 18px;
  padding: 18px 18px 17px;
  background: #fff;
  border-bottom: 1px solid #edf0f4;
  font-size: 18px;
}

.control-category + .control-category {
  margin-top: 22px;
}

.control-category-header {
  width: 100%;
  min-height: 34px;
  border: 0;
  background: transparent;
  color: #141b24;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px;
}

.control-category-header strong {
  font-size: 16px;
}

.control-category-header svg {
  width: 18px;
  height: 18px;
  color: #8b95a1;
}

.control-card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.control-card {
  position: relative;
  min-width: 0;
  height: 58px;
  border: 1px solid #e1e6ed;
  border-radius: 8px;
  background: #fff;
  color: #263445;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  text-align: left;
  font-weight: 600;
  overflow: hidden;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.control-card:hover {
  border-color: #8cc4ff;
  box-shadow: 0 6px 16px rgba(15, 125, 255, 0.12);
  transform: translateY(-1px);
}

.control-card.disabled {
  color: #a1a9b4;
  background: #f8fafc;
  box-shadow: none;
  transform: none;
  cursor: not-allowed;
}

.control-card-icon {
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  color: #1f2937;
  font-size: 14px;
}

.control-card-icon svg {
  width: 21px;
  height: 21px;
}

.control-card > span:last-of-type {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.control-card em {
  position: absolute;
  right: 8px;
  top: 6px;
  border: 1px solid #ffb454;
  border-radius: 4px;
  padding: 0 4px;
  color: #ff8a00;
  font-style: normal;
  font-size: 11px;
  line-height: 16px;
  background: #fff7ed;
}

.control-category-note {
  margin: 14px 0 0;
  color: #8b95a1;
  font-size: 13px;
}

.form-designer-stage {
  min-width: 0;
  display: grid;
  grid-template-rows: 56px minmax(0, 1fr);
}

.form-designer-toolbar {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 30px;
  background: #fff;
  border-bottom: 1px solid #e5e9ef;
}

.device-switch {
  height: 40px;
  display: inline-flex;
  align-items: center;
  border-radius: 8px;
  background: #eef0f3;
  padding: 3px;
}

.device-switch button,
.history-actions button {
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  display: grid;
  place-items: center;
}

.device-switch button[aria-pressed='true'] {
  background: #fff;
  color: #111827;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.14);
}

.device-switch svg {
  width: 20px;
  height: 20px;
}

.history-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.history-actions button {
  font-size: 22px;
}

.history-actions button:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.preview-feature-btn {
  margin-left: auto;
  height: 40px;
  border: 0;
  border-radius: 8px;
  background: #f1f3f6;
  color: #1f2937;
  padding: 0 14px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
}

.phone-preview {
  position: relative;
  align-self: start;
  justify-self: center;
  width: min(420px, calc(100% - 72px));
  min-height: 640px;
  max-height: calc(100vh - 270px);
  margin: 36px auto 50px;
  border: 14px solid #fff;
  border-radius: 30px;
  background: #f3f5f7;
  box-shadow: 0 24px 54px rgba(26, 36, 56, 0.16);
  overflow: hidden;
}

.phone-preview--desktop {
  width: min(460px, calc(100% - 72px));
}

.phone-title {
  min-height: 62px;
  display: flex;
  align-items: center;
  padding: 0 28px;
  background: #fff;
  border-bottom: 1px solid #edf0f4;
  color: #1f2937;
  font-size: 20px;
  font-weight: 700;
}

.phone-tip {
  padding: 20px;
  color: #6b7280;
  background: #fff;
  border-bottom: 1px solid #e9edf2;
}

.phone-empty {
  min-height: 220px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 18px;
  color: #6b7280;
}

.phone-form {
  max-height: calc(100vh - 260px);
  overflow-y: auto;
}

.empty-doc {
  width: 56px;
  height: 70px;
  border: 2px solid #dbe3ec;
  background: repeating-linear-gradient(#dbe3ec 0 3px, transparent 3px 12px);
}

.phone-line-row {
  position: relative;
}

.phone-line-row.dragging {
  opacity: 0.72;
}

.phone-line-row.drag-over::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: -1px;
  border-top: 2px solid #1e9af0;
  z-index: 2;
}

.phone-line, .phone-area, .phone-attachment {
  min-height: 58px;
  display: flex;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 46px 16px 20px;
  background: #fff;
  border: 0;
  border-bottom: 1px solid #edf0f4;
  text-align: left;
  width: 100%;
  cursor: move;
}

.phone-line.active {
  outline: 0;
  box-shadow: inset 3px 0 0 #168dff;
}

.phone-line--layout {
  min-height: 72px;
  padding: 14px 46px 14px 20px;
  background: #f7f8fa;
}

.phone-line--compound,
.phone-line--block {
  display: block;
  padding: 0 46px 0 0;
}

.phone-layout-preview {
  min-height: 44px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  align-items: stretch;
}

.phone-layout-preview span {
  min-height: 44px;
  border: 1px dashed #cdd5df;
  border-radius: 6px;
  background: #fff;
}

.phone-compound-lines > div {
  min-height: 58px;
  padding: 0 0 0 20px;
  border-bottom: 1px solid #edf0f4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.phone-compound-lines > div:last-child {
  border-bottom: 0;
}

.phone-attachment-preview,
.phone-detail-preview,
.phone-static-preview,
.phone-number-preview {
  min-height: 96px;
  padding: 16px 20px;
  display: grid;
  gap: 12px;
}

.phone-attachment-preview {
  min-height: 58px;
  grid-template-columns: 76px minmax(0, 1fr);
  align-items: center;
  gap: 16px;
}

.phone-attachment-preview b {
  color: #111827;
  font-weight: 500;
}

.phone-attachment-preview b span {
  color: #111827;
}

.attachment-add-button {
  justify-self: start;
  min-width: 120px;
  height: 34px;
  border: 1px solid #d8dde6;
  border-radius: 5px;
  background: #fff;
  color: #111827;
  font-size: 14px;
  cursor: pointer;
}

.attachment-add-button:hover {
  border-color: #1677ff;
  color: #1677ff;
}

.attachment-tile {
  width: 48px;
  height: 48px;
  border: 1px solid #d9dfe7;
  border-radius: 4px;
  display: grid;
  place-items: center;
  color: #c2c8d1;
  font-size: 24px;
}

.phone-detail-preview {
  min-height: 118px;
  display: grid;
  gap: 16px;
  color: #7a8491;
  background: #fff;
}

.phone-detail-preview b {
  color: #111827;
  font-weight: 500;
}

.detail-card-preview {
  overflow: hidden;
  border: 1px solid #e1e5eb;
  border-radius: 6px;
  background: #fff;
}

.detail-card-preview header {
  min-height: 36px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: #f7f8fa;
  border-bottom: 1px solid #edf0f4;
}

.detail-card-preview header button {
  border: 0;
  background: transparent;
  color: #1677ff;
  font-size: 13px;
  cursor: pointer;
}

.detail-child-form {
  display: grid;
}

.detail-child-form-row {
  min-height: 48px;
  padding: 8px 12px;
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr) 26px;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid #edf0f4;
  cursor: pointer;
}

.detail-child-form-row:last-child {
  border-bottom: 0;
}

.detail-child-form-row.active {
  background: #f6fbff;
  box-shadow: inset 2px 0 0 #1677ff;
}

.phone-detail-preview .detail-child-form-row span {
  color: #111827;
  font-weight: 500;
}

.phone-detail-preview .detail-child-form-row i {
  margin-right: 1px;
  color: #e5484d;
  font-style: normal;
}

.detail-child-form-row em,
.detail-summary-preview em {
  min-height: 32px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  border: 1px solid #d8dde6;
  border-radius: 4px;
  background: #fff;
  color: #b3bbc6;
  font-style: normal;
}

.detail-child-inline-remove {
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 50%;
  background: #edf2f7;
  color: #6b7280;
  cursor: pointer;
}

.detail-child-inline-remove:hover {
  background: #e1e9f2;
  color: #111827;
}

.detail-add-child-btn {
  min-height: 36px;
  border: 1px solid #d8dde6;
  border-radius: 5px;
  background: #fff;
  color: #1677ff;
  font-weight: 500;
  cursor: pointer;
}

.detail-add-child-btn:hover {
  border-color: #1677ff;
}

.detail-summary-preview {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  align-items: center;
  gap: 14px;
}

.phone-detail-preview .detail-summary-preview span {
  color: #8a95a3;
  line-height: 20px;
}

.detail-summary-preview em {
  background: #f7f8fa;
}

.detail-child-list {
  width: 100%;
  display: grid;
  gap: 8px;
}

.detail-child-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 26px;
  align-items: center;
  gap: 8px;
  padding: 0 20px;
}

.detail-child-chip {
  min-width: 0;
  min-height: 36px;
  border: 1px solid #d9dfe7;
  border-radius: 4px;
  background: #fff;
  color: #39465a;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  text-align: left;
  cursor: pointer;
}

.detail-child-chip.active {
  border-color: #1e9af0;
  box-shadow: 0 0 0 1px #1e9af0 inset;
}

.detail-child-chip span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #39465a;
}

.detail-child-chip small {
  margin-left: auto;
  color: #9aa3ad;
  font-size: 12px;
}

.detail-child-remove {
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 50%;
  background: #edf2f7;
  color: #6b7280;
  cursor: pointer;
}

.phone-static-preview {
  min-height: 58px;
  align-content: center;
  color: #8a95a3;
}

.phone-number-preview span {
  display: flex;
  align-items: center;
  gap: 12px;
}

.phone-number-preview em {
  color: #202936;
  font-style: normal;
}

.phone-number-preview small {
  width: 1px;
  height: 24px;
  background: #d9dfe7;
}

.phone-field-actions {
  position: absolute;
  right: 18px;
  top: 12px;
  z-index: 3;
  min-height: 30px;
  padding: 0 6px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border-radius: 16px;
  background: #f7f8fa;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
}

.phone-field-actions button {
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: #111827;
  display: grid;
  place-items: center;
  cursor: pointer;
}

.phone-field-actions button:hover {
  background: #d9e6f5;
  color: #2a3546;
}

.phone-field-actions svg {
  width: 16px;
  height: 16px;
}

.phone-area {
  min-height: 112px;
  flex-direction: column;
}

.phone-line b, .phone-area b {
  font-weight: 500;
}

.phone-line span, .phone-area span {
  color: #9aa3ad;
}

.phone-line i, .phone-area i {
  color: #e53935;
  font-style: normal;
}

.phone-control-actions {
  position: relative;
}

.phone-control-drop-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 72px;
  border: 0;
  border-top: 1px solid #d9dfe7;
  background: #eef1f4;
  color: #8b95a1;
  font-size: 15px;
  text-align: center;
}

.phone-control-drop-hint svg {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
}

.config-panel {
  max-width: 620px;
}

.designer-config-panel {
  max-width: none;
  max-height: calc(100vh - 186px);
  overflow-y: auto;
  padding: 0 0 60px;
  background: #fff;
  border-left: 1px solid #e5e9ef;
}

.config-panel h2 {
  font-size: 18px;
  margin: 4px 0 18px;
  padding-bottom: 18px;
  border-bottom: 1px solid #e9edf2;
}

.config-panel label, .filter-grid label {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  align-items: center;
  margin-bottom: 24px;
}

.config-panel label > span:first-child, .filter-grid span {
  color: #747e8b;
}

.config-panel input, .config-panel select, .filter-grid input, .filter-grid select {
  height: 42px;
  border: 1px solid #d9dfe7;
  border-radius: 4px;
  padding: 0 14px;
  background: #fff;
}

.icon-field {
  min-height: 72px;
}

.icon-field .template-icon {
  width: 68px;
  height: 68px;
}

.icon-field .template-icon :deep(svg) {
  width: 34px;
  height: 34px;
}

.radio-row {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-bottom: 30px;
}

.radio-row > span {
  width: 120px;
  color: #747e8b;
}

.control-config {
  margin-top: 34px;
}

.checkbox-config-row {
  display: grid;
  grid-template-columns: 120px repeat(3, minmax(0, 1fr));
  align-items: center;
  gap: 18px;
  margin-bottom: 24px;
}

.checkbox-config-row > span {
  color: #747e8b;
}

.checkbox-config-row label {
  display: flex;
  grid-template-columns: none;
  gap: 8px;
  align-items: center;
  margin-bottom: 0;
}

.checkbox-config-row input {
  width: 16px;
  height: 16px;
  padding: 0;
}

.control-extra-row {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  align-items: center;
  gap: 12px 18px;
  margin-bottom: 24px;
}

.control-extra-row > span {
  color: #747e8b;
}

.control-extra-row label {
  display: inline-flex;
  grid-template-columns: none;
  gap: 8px;
  align-items: center;
  margin-bottom: 0;
}

.control-extra-row input[type='checkbox'],
.control-extra-row input[type='radio'] {
  width: 16px;
  height: 16px;
  padding: 0;
}

.detail-summary-fields {
  align-items: start;
}

.detail-summary-fields p {
  margin: 0;
  color: #8b96a5;
  font-size: 13px;
  line-height: 18px;
}

.options-config-row {
  align-items: start !important;
}

.options-config-row textarea {
  min-height: 96px;
  border: 1px solid #d9dfe7;
  border-radius: 4px;
  padding: 10px 14px;
  resize: vertical;
}

.options-hint {
  grid-column: 2;
  margin: -12px 0 0;
  color: #8b96a5;
  font-size: 13px;
  line-height: 18px;
}

.leave-type-table {
  display: grid;
  grid-template-columns: 1.3fr 1fr 1fr;
  border: 1px solid #d9dfe7;
  margin-left: 120px;
}

.leave-type-table > * {
  padding: 10px 14px;
  border-right: 1px solid #e5e9ee;
  border-bottom: 1px solid #e5e9ee;
}

.leave-type-table > div {
  background: #f7f8fa;
  color: #6b7280;
}

.hint-box {
  margin: 28px 0 0 120px;
  padding: 14px;
  background: #f3f5f7;
  color: #8b95a1;
}

.designer-config-panel .field-config-heading {
  min-height: 72px;
  padding: 0 28px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid #edf0f4;
  background: #fff;
  position: sticky;
  top: 0;
  z-index: 4;
}

.designer-config-panel .field-config-heading h2 {
  margin: 0;
  padding: 0;
  border: 0;
  color: #111827;
  font-size: 17px;
  line-height: 24px;
}

.field-config-icon {
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  color: #1f2937;
  font-size: 14px;
  font-weight: 800;
}

.field-config-icon svg {
  width: 20px;
  height: 20px;
}

.field-config-body {
  padding: 22px 28px 32px;
}

.designer-config-panel .field-config-line {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  align-items: start;
  margin: 0 0 28px;
}

.designer-config-panel .field-config-line > span:first-child,
.field-config-section h3,
.designer-switch-row > span:first-child {
  color: #1f2937;
  font-size: 16px;
  font-weight: 700;
}

.field-input-with-count {
  position: relative;
  display: block;
  min-width: 0;
}

.designer-config-panel .field-input-with-count input,
.designer-config-panel .field-config-line select,
.designer-config-panel .field-default-input {
  width: 100%;
  height: 46px;
  border: 1px solid #d9dee7;
  border-radius: 5px;
  background: #fff;
  color: #111827;
  padding: 0 72px 0 16px;
  font-size: 15px;
}

.field-input-with-count small {
  position: absolute;
  top: 50%;
  right: 14px;
  transform: translateY(-50%);
  color: #9aa3ad;
  font-size: 15px;
}

.designer-config-panel textarea {
  width: 100%;
  min-height: 118px;
  border: 1px solid #d9dee7;
  border-radius: 5px;
  padding: 12px 14px;
  resize: vertical;
}

.designer-text-link,
.designer-feature-tip button,
.field-option-links button {
  border: 0;
  background: transparent;
  color: #168dff;
  padding: 0;
  font-weight: 600;
}

.field-config-section {
  margin: 0 0 30px;
}

.field-config-section h3 {
  margin: 0 0 14px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.field-config-section h3 small {
  color: #9aa3ad;
  font-size: 13px;
  font-weight: 500;
}

.field-option-list {
  display: grid;
  gap: 10px;
}

.field-option-row {
  display: grid;
  grid-template-columns: 22px 20px minmax(0, 1fr) 26px 26px;
  align-items: center;
  gap: 8px;
}

.field-option-default {
  width: 18px;
  height: 18px;
  border: 1px solid #cfd6df;
  border-radius: 50%;
  background: #fff;
}

.field-option-default.active {
  border: 5px solid #168dff;
}

.field-option-drag,
.field-option-icon-btn {
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #8b95a1;
  display: grid;
  place-items: center;
}

.field-option-drag {
  width: 20px;
  cursor: grab;
  font-size: 16px;
  line-height: 1;
}

.field-option-icon-btn:hover {
  background: #eef5ff;
  color: #168dff;
}

.field-option-icon-btn svg {
  width: 18px;
  height: 18px;
}

.field-option-row input {
  min-width: 0;
  height: 42px;
  border: 1px solid #d9dee7;
  border-radius: 4px;
  padding: 0 12px;
}

.field-option-links {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 12px;
  font-size: 13px;
}

.designer-radio-line {
  display: flex !important;
  grid-template-columns: none !important;
  align-items: center;
  gap: 10px;
  margin: 0 0 12px !important;
  color: #1f2937;
}

.designer-radio-line.disabled {
  color: #b8c0cb;
}

.designer-radio-line input {
  width: 18px;
  height: 18px;
  padding: 0;
}

.designer-feature-tip {
  margin: -2px 0 16px;
  color: #8b95a1;
  font-size: 14px;
}

.field-default-input {
  margin-top: 10px;
  padding-right: 16px !important;
}

.field-validate-btn {
  min-width: 154px;
  height: 42px;
  border: 1px solid #d9dee7;
  border-radius: 5px;
  background: #fff;
  color: #111827;
  font-weight: 700;
}

.designer-switch-list {
  display: grid;
  gap: 26px;
  margin-top: 8px;
}

.designer-switch-row {
  display: flex !important;
  grid-template-columns: none !important;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin: 0 !important;
}

.designer-switch-row--compact {
  margin-top: 20px !important;
}

.designer-switch-row small {
  margin-left: 6px;
  color: #9aa3ad;
  font-size: 13px;
  font-weight: 500;
}

.designer-switch-row.disabled > span:first-child,
.designer-switch-row.disabled small {
  color: #b8c0cb;
}

.designer-switch {
  position: relative;
  width: 64px;
  height: 34px;
  flex: 0 0 auto;
}

.designer-switch input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}

.designer-switch i {
  position: absolute;
  inset: 0;
  border-radius: 18px;
  background: #b8b8b8;
  transition: background 0.16s ease;
}

.designer-switch i::after {
  content: '';
  position: absolute;
  left: 3px;
  top: 3px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.18);
  transition: transform 0.16s ease;
}

.designer-switch input:checked + i {
  background: #168dff;
}

.designer-switch input:checked + i::after {
  transform: translateX(30px);
}

.designer-switch input:disabled {
  cursor: not-allowed;
}

.designer-switch input:disabled + i {
  background: #d5d9df;
}

.field-advanced-block {
  margin-top: 34px;
  padding-top: 24px;
  border-top: 1px solid #edf0f4;
}

.field-advanced-block .control-extra-row,
.field-advanced-block .duration-config-box {
  grid-template-columns: 92px minmax(0, 1fr);
}

.designer-save-btn {
  margin-top: 28px;
}

.designer-config-panel .hint-box {
  margin-left: 0;
}

.designer-config-panel .leave-type-table {
  margin-left: 0;
}

.flow-card {
  width: 150px;
  min-height: 52px;
  padding: 10px;
  border: 1px solid #e0e5ec;
  border-top: 4px solid #5ca8ff;
  background: #fff;
  text-align: left;
  font-size: 12px;
}

.flow-card small {
  display: block;
  color: #737f8d;
  margin-top: 4px;
}

.flow-approval {
  border-top-color: #f5ae33;
}

.flow-handler {
  border-top-color: #2ebbb7;
}

.flow-notify {
  border-top-color: #9b7af5;
}

.flow-condition {
  border-top-color: #3b82f6;
}

.flow-parallel {
  border-top-color: #16a3a8;
}

.flow-auto {
  border-top-color: #8b95a1;
}

.flow-end {
  width: 140px;
  padding: 9px;
  background: #fff;
  border: 1px solid #e0e5ec;
  text-align: center;
  font-size: 12px;
}

.save-btn {
  justify-self: start;
}

.records-view {
  padding: 48px 56px;
}

.archive-manager {
  min-height: auto;
  margin-top: 24px;
  padding: 32px 40px 40px;
}

.archive-manager--overview {
  margin-top: 0;
}

.archive-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
}

.archive-header h2 {
  margin: 0;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px 42px;
}

.archive-filter-grid {
  align-items: end;
}

.archive-filter-actions {
  display: flex;
  gap: 12px;
}

.archive-applicant-control {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.archive-applicant-trigger {
  min-width: 0;
  height: 42px;
  border: 1px solid #d9dfe7;
  border-radius: 4px;
  background: #fff;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #202936;
  text-align: left;
}

.archive-applicant-trigger span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #202936;
}

.archive-applicant-trigger .archive-applicant-placeholder {
  color: #8b95a1;
}

.archive-applicant-clear {
  height: 42px;
  border: 1px solid #d9dfe7;
  border-radius: 4px;
  background: #fff;
  color: #2f7de1;
  padding: 0 12px;
}

.record-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 34px 0 24px;
}

.record-tools h3 {
  margin: 0;
  font-size: 22px;
  font-weight: 500;
}

.record-tools div {
  display: flex;
  gap: 18px;
}

.record-table {
  width: 100%;
  border-collapse: collapse;
  border: 1px solid #e0e5ec;
}

.record-table th {
  background: #f7f8fa;
  color: #6b7280;
  font-weight: 500;
  text-align: left;
}

.record-table th, .record-table td {
  padding: 16px;
  border-bottom: 1px solid #edf0f4;
  white-space: pre-line;
}

.record-empty {
  text-align: center;
  color: #6b7280;
}

.blur-name, .blur-dept {
  display: block;
  height: 18px;
  margin-bottom: 6px;
  background: linear-gradient(90deg, #cfcfcf, #e5e5e5, #cfcfcf);
  filter: blur(3px);
}

.blur-name { width: 70px; }
.blur-dept { width: 170px; }

.more-dot {
  border: 0;
  background: transparent;
  color: #1e63a9;
  font-size: 22px;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.58);
  display: grid;
  place-items: center;
  z-index: 50;
}

.template-move-modal {
  width: min(430px, calc(100vw - 36px));
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.22);
  overflow: hidden;
}

.template-move-header {
  min-height: 58px;
  padding: 0 20px 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #edf1f6;
}

.template-move-header h2 {
  min-width: 0;
  margin: 0;
  color: #111827;
  font-size: 18px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-move-close {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: #8b95a1;
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
}

.template-move-close:hover {
  background: #f3f6fa;
  color: #1f2937;
}

.template-move-body {
  padding: 10px 0;
}

.template-move-new-row {
  min-height: 50px;
  margin: 0 18px 8px;
  padding: 0 8px 0 14px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 30px 30px;
  align-items: center;
  gap: 6px;
  border: 1px solid #d9e2ee;
  border-radius: 8px;
  background: #fff;
}

.template-move-new-row input {
  min-width: 0;
  height: 38px;
  border: 0;
  outline: 0;
  color: #111827;
  font-size: 15px;
}

.template-move-new-row span {
  color: #9aa3ad;
  font-size: 13px;
  white-space: nowrap;
}

.template-move-new-row button {
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: #7a8491;
  cursor: pointer;
}

.template-move-new-row button:hover {
  background: #eef5ff;
  color: #0f6dff;
}

.template-move-group-list {
  display: grid;
}

.template-move-group {
  min-height: 48px;
  border: 0;
  background: #fff;
  padding: 0 24px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  color: #1f2937;
  font-size: 15px;
  text-align: left;
  cursor: pointer;
}

.template-move-group:hover {
  background: #f7faff;
}

.template-move-group.current {
  cursor: default;
}

.template-move-group.current:hover {
  background: #fff;
}

.template-move-group span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-move-group em {
  min-height: 24px;
  border-radius: 999px;
  background: #f2f4f7;
  color: #7a8491;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  font-style: normal;
  font-size: 13px;
}

.template-move-group i {
  width: 18px;
  height: 18px;
  border: 1.5px solid #c9d2df;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.template-move-group i.checked {
  border-color: #0f6dff;
}

.template-move-group i.checked::after {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #0f6dff;
}

.template-move-footer {
  min-height: 64px;
  padding: 0 20px 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-top: 1px solid #edf1f6;
}

.template-move-footer > div {
  display: flex;
  align-items: center;
  gap: 10px;
}

.template-move-add {
  border: 0;
  background: transparent;
  color: #0f6dff;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  cursor: pointer;
}

.template-move-add svg {
  width: 16px;
  height: 16px;
}

.template-sort-modal {
  width: min(560px, calc(100vw - 36px));
  max-height: min(720px, calc(100vh - 48px));
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.22);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  overflow: hidden;
}

.template-sort-header {
  min-height: 72px;
  padding: 18px 20px 16px 24px;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #edf1f6;
}

.template-sort-header h2 {
  margin: 0;
  color: #111827;
  font-size: 18px;
  font-weight: 700;
}

.template-sort-header p {
  margin: 6px 0 0;
  color: #7a8491;
  font-size: 13px;
}

.template-sort-list {
  padding: 12px 16px;
  overflow: auto;
  display: grid;
  gap: 8px;
}

.template-sort-item {
  min-height: 62px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  padding: 10px 12px;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}

.template-sort-item.disabled {
  background: #fafbfc;
}

.template-sort-index {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: #eef5ff;
  color: #0f6dff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

.template-sort-main {
  min-width: 0;
}

.template-sort-main strong,
.template-sort-main small {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-sort-main strong {
  color: #111827;
  font-size: 15px;
}

.template-sort-main small {
  margin-top: 4px;
  color: #8b95a1;
  font-size: 13px;
}

.template-sort-actions {
  display: inline-flex;
  gap: 8px;
}

.template-sort-actions button {
  height: 32px;
  border: 1px solid #d9e2ee;
  border-radius: 6px;
  background: #fff;
  color: #1f2937;
  padding: 0 10px;
}

.template-sort-actions button:disabled {
  cursor: not-allowed;
  color: #b8c0cc;
  background: #f6f8fb;
}

.template-sort-footer {
  min-height: 64px;
  padding: 0 20px 0 24px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  border-top: 1px solid #edf1f6;
}

.add-modal, .flow-dialog, .simulate-dialog {
  position: relative;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 16px 60px rgba(0, 0, 0, 0.18);
}

.add-modal {
  width: 840px;
  max-height: 72vh;
  overflow: auto;
  padding: 34px 62px 22px;
}

.modal-close {
  position: absolute;
  right: 24px;
  top: 22px;
  border: 0;
  background: transparent;
  color: #a0a7b0;
  font-size: 28px;
}

.add-modal h2, .flow-dialog h2, .simulate-dialog h2 {
  margin: 0 0 28px;
  font-size: 20px;
}

.custom-template {
  border: 0;
  background: transparent;
  color: #1e63a9;
  font-size: 18px;
  margin-bottom: 24px;
}

.modal-tabs {
  display: flex;
  gap: 44px;
  padding: 24px 0;
  border-top: 1px solid #edf0f4;
}

.recommend-block {
  margin: 18px 0 34px;
}

.recommend-block > span {
  color: #8b95a1;
}

.recommend-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px 8px;
  margin-top: 18px;
}

.recommend-grid button {
  height: 58px;
  border: 1px solid #e0e5ec;
  background: #fff;
  border-radius: 4px;
}

.recommend-grid button.selected {
  border-color: #9cc8f3;
  background: #f8fbff;
}

.flow-dialog {
  width: min(1100px, 92vw);
  padding: 34px 44px;
}

.flow-designer-view {
  position: relative;
  padding: 0;
  background: #f5f6f8;
}

.flow-designer-toolbar {
  min-height: 58px;
  padding: 8px 16px 8px 24px;
  border-bottom: 1px solid #e6ebf2;
  background: #fff;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  position: sticky;
  top: 0;
  z-index: 20;
}

.flow-version-control {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
}

.flow-version-trigger {
  min-height: 42px;
  border: 0;
  border-radius: 8px;
  background: #f3f5f7;
  padding: 0 12px 0 14px;
  color: #1f2937;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 17px;
  font-weight: 700;
  box-shadow: inset 0 0 0 1px rgba(31, 41, 55, 0.02);
}

.flow-version-trigger--draft {
  background: #f7f8fa;
}

.flow-version-trigger span {
  color: #7b8492;
  font-size: 16px;
}

.flow-version-trigger b,
.flow-version-option b {
  min-height: 22px;
  border-radius: 4px;
  padding: 1px 6px 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-style: normal;
  line-height: 1;
}

.flow-version-status--active {
  border: 1px solid #83d59c;
  color: #16a34a;
  background: #f0fff4;
}

.flow-version-status--draft {
  border: 1px solid #ffbd72;
  color: #f97316;
  background: #fff7ed;
}

.flow-save-state {
  display: inline-flex;
  align-items: center;
  color: #9aa3ad;
  font-size: 15px;
}

.flow-version-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 40;
  width: 360px;
  border: 1px solid #eef1f5;
  border-radius: 8px;
  background: #fff;
  padding: 10px 0;
  box-shadow: 0 18px 46px rgba(15, 23, 42, 0.14);
}

.flow-version-option {
  width: 100%;
  min-height: 76px;
  border: 0;
  background: #fff;
  padding: 13px 20px;
  color: #1f2937;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  text-align: left;
}

.flow-version-option:hover:not(:disabled),
.flow-version-option.active {
  background: #f7faff;
}

.flow-version-option span {
  display: grid;
  gap: 8px;
}

.flow-version-option strong {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 17px;
}

.flow-version-option small {
  color: #9aa3ad;
  font-size: 14px;
}

.flow-version-option i {
  color: #1f2937;
  font-size: 24px;
  font-style: normal;
}

.flow-workbench-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  align-items: center;
}

.flow-workbench-actions .secondary-btn,
.flow-workbench-actions .primary-btn {
  min-width: 86px;
  height: 38px;
  padding: 0 14px;
}

.flow-zoom-control {
  position: absolute;
  top: 24px;
  right: 10px;
  z-index: 18;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  border: 0;
  background: transparent;
}

.flow-zoom-control button {
  min-width: 45px;
  height: 42px;
  border: 0;
  border-radius: 8px;
  background: #fff;
  color: #344054;
  font-size: 20px;
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.12);
}

.flow-zoom-control button:nth-child(2) {
  min-width: 62px;
  box-shadow: none;
  background: transparent;
  font-size: 18px;
  font-weight: 600;
}

.flow-canvas-shell {
  position: relative;
}

.flow-drawer-backdrop {
  position: fixed;
  inset: 192px 0 0;
  z-index: 70;
  background: rgba(15, 23, 42, 0.28);
}

.flow-config-drawer {
  position: fixed;
  top: 192px;
  right: 0;
  bottom: 0;
  z-index: 80;
  width: clamp(420px, 36vw, 520px);
  max-width: min(520px, calc(100vw - 320px));
  border-radius: 8px 8px 0 0;
  border-width: 1px 1px 0;
  padding: 24px 20px 32px;
  max-height: none !important;
  box-shadow: -8px 0 28px rgba(15, 23, 42, 0.12);
}

.flow-drawer-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.flow-drawer-header--condition {
  min-height: 50px;
  padding-bottom: 16px;
  border-bottom: 1px solid #edf0f4;
}

.condition-drawer-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.condition-drawer-title input {
  width: min(240px, 26vw);
  border: 0;
  background: transparent;
  color: #111827;
  font-size: 18px;
  font-weight: 700;
  outline: none;
  padding: 0;
}

.condition-drawer-title span {
  color: #111827;
  font-size: 18px;
  line-height: 1;
}

.branch-priority-select {
  width: 112px;
  min-height: 38px !important;
  border: 1px solid #dfe5ee;
  border-radius: 6px;
  background: #fff;
  color: #1f2937;
  font-size: 15px;
  padding: 0 10px;
}

.condition-info-icon {
  width: 16px;
  height: 16px;
  border: 1px solid #a8b0bb;
  border-radius: 50%;
  color: #8b95a1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-style: italic;
  font-weight: 700;
}

.flow-setting-layout {
  display: grid;
  grid-template-columns: minmax(320px, 430px) minmax(380px, 1fr);
  gap: 20px;
  min-height: 480px;
  margin-bottom: 20px;
}

.flow-setting-layout--page {
  grid-template-columns: minmax(320px, 460px) minmax(420px, 1fr);
  min-height: calc(100vh - 300px);
  margin-bottom: 0;
}

.flow-lane {
  background: #f3f5f8;
  border-radius: 8px;
  border: 1px solid #e3e8ef;
  padding: 20px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  max-height: 62vh;
  overflow: auto;
}

.flow-setting-layout--page .flow-lane,
.flow-setting-layout--page .flow-side-panel {
  max-height: calc(100vh - 315px);
}

.flow-node-card {
  width: 240px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.flow-node-card.active {
  box-shadow: 0 0 0 2px rgba(47, 125, 225, 0.2);
}

.node-open-btn {
  border: 0;
  background: transparent;
  font-size: 24px;
  color: #8c95a1;
  line-height: 1;
  padding: 0 2px;
}

.flow-drawer-actions .node-open-btn {
  font-size: 26px;
}

.flow-plus {
  width: 30px;
  height: 30px;
  border-radius: 999px;
  border: 1px solid #cad3df;
  background: #fff;
  color: #667080;
  font-size: 22px;
  line-height: 20px;
  display: grid;
  place-items: center;
  padding: 0;
}

.flow-side-panel {
  border: 1px solid #e3e8ef;
  border-radius: 8px;
  background: #fff;
  padding: 18px 18px 16px;
  max-height: 62vh;
  overflow: auto;
}

.flow-side-panel.flow-config-drawer {
  padding: 24px 20px 32px;
}

.flow-side-panel header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 26px;
}

.flow-side-panel h3 {
  margin: 0;
  font-size: 22px;
  line-height: 1.35;
  font-weight: 800;
}

.node-config-chrome {
  display: grid;
  gap: 22px;
  margin-bottom: 24px;
}

.node-config-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  background: #e8ebef;
  border-radius: 6px;
  overflow: hidden;
  margin: 0;
}

.node-config-tabs button {
  position: relative;
  min-height: 54px;
  border: 0;
  border-right: 1px solid #d8dde5;
  background: transparent;
  color: #667085;
  font-size: 16px;
  font-weight: 700;
}

.node-config-tabs button:last-child {
  border-right: 0;
}

.node-config-tabs button[aria-selected='true'] {
  background: #fff;
  color: #111827;
  box-shadow: 0 1px 6px rgba(15, 23, 42, 0.12);
}

.node-config-tabs button[aria-selected='true']::after {
  content: "";
  position: absolute;
  left: 50%;
  bottom: -1px;
  width: 38px;
  height: 4px;
  border-radius: 999px;
  background: #111827;
  transform: translateX(-50%);
}

.node-config-tabs button:disabled {
  cursor: not-allowed;
  color: #8b95a1;
}

.node-config-section {
  display: grid;
  gap: 26px;
}

.node-config-section h4,
.node-config-block h4 {
  margin: 0 0 18px;
  font-size: 18px;
  line-height: 1.35;
  font-weight: 800;
}

.node-config-block--approval-type h4 {
  margin-bottom: 18px;
}

.node-radio-line,
.node-radio-stack {
  display: grid;
  gap: 12px;
}

.node-radio-line {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 28px;
}

.flow-side-panel .node-radio-line label,
.flow-side-panel .node-radio-stack label {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  color: #111827;
  font-size: 16px;
  font-weight: 600;
}

.flow-side-panel input[type='radio'],
.flow-side-panel input[type='checkbox'] {
  accent-color: #1688ff;
}

.assignee-option-card {
  border: 1px solid #e4e8ee;
  border-radius: 8px;
  padding: 28px 30px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 22px;
}

.assignee-option-card--notify {
  background: #fff;
}

.assignee-option-card--approval {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  padding: 28px 30px;
}

.assignee-option-card--approval .assignee-option-group {
  grid-template-columns: 1fr;
  column-gap: 0;
  row-gap: 18px;
}

.assignee-option-card--approval .assignee-option-group:not(:last-child) {
  padding-right: 30px;
  border-right: 1px solid #edf1f5;
}

.assignee-option-card--approval .assignee-option-group:not(:first-child) {
  padding-left: 30px;
}

.assignee-option-card--approval .assignee-option-group strong {
  grid-column: 1 / -1;
}

.assignee-option-group {
  display: grid;
  gap: 16px;
  align-content: start;
}

.assignee-option-group strong {
  color: #111827;
  font-size: 16px;
  font-weight: 800;
}

.flow-config-drawer .assignee-option-card {
  padding: 22px 24px;
}

.flow-config-drawer .assignee-option-card--approval {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px 0;
}

.flow-config-drawer .assignee-option-card--approval .assignee-option-group {
  grid-template-columns: 1fr;
  row-gap: 14px;
}

.flow-config-drawer .assignee-option-card--approval .assignee-option-group:not(:last-child) {
  padding-right: 22px;
  border-right: 1px solid #edf1f5;
}

.flow-config-drawer .assignee-option-card--approval .assignee-option-group:not(:first-child) {
  padding-left: 22px;
}

.flow-config-drawer .assignee-option-card--approval .assignee-option-group:nth-child(2) {
  border-right: 0;
}

.flow-config-drawer .assignee-option-card--approval .assignee-option-group:nth-child(3) {
  grid-column: 1 / -1;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  padding: 18px 0 0;
  border-top: 1px solid #edf1f5;
  border-right: 0;
}

.flow-config-drawer .assignee-option-card--approval .assignee-option-group:nth-child(3) strong {
  grid-column: 1 / -1;
}

.choice-option {
  min-height: 40px;
  border: 0;
  background: transparent;
  color: #1f2937;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0;
  text-align: left;
  font-size: 16px;
  font-weight: 600;
}

.flow-config-drawer .choice-option {
  min-height: 34px;
  gap: 10px;
  font-size: 15px;
  line-height: 1.35;
}

.choice-option > span:first-child {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  border: 2px solid #d8dde5;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.choice-option.selected > span:first-child {
  border-color: #1688ff;
  box-shadow: inset 0 0 0 5px #fff;
  background: #1688ff;
}

.choice-option.disabled {
  color: #b3bac4;
  cursor: not-allowed;
}

.blocked-option-icon {
  border-color: #cfd5dd !important;
  color: #9aa3ad;
  font-size: 12px;
  line-height: 1;
}

.choice-option em {
  border: 1px solid #f3a73f;
  color: #f08a00;
  border-radius: 4px;
  padding: 0 5px;
  font-size: 12px;
  font-style: normal;
}

.node-inline-field {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

.node-inline-field > span {
  color: #8b95a1;
  font-weight: 700;
}

.flow-side-panel label {
  display: grid;
  gap: 7px;
  font-size: 14px;
  margin-bottom: 12px;
}

.flow-side-panel .node-radio-line label,
.flow-side-panel .node-radio-stack label {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 0;
  color: #111827;
  font-size: 16px;
  font-weight: 600;
}

.flow-side-panel .node-inline-field {
  grid-template-columns: 110px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

.flow-side-panel select {
  min-height: 38px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 0 10px;
}

.member-picker-inline {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
}

.member-picker-inline input {
  min-height: 38px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 0 10px;
}

.node-assignee-detail {
  display: grid;
  gap: 10px;
}

.node-assignee-detail h4 {
  display: flex;
  align-items: center;
  gap: 8px;
}

.node-assignee-detail h4 small {
  color: #8b95a1;
  font-size: 14px;
  font-weight: 600;
}

.member-picker-bar,
.role-add-bar,
.node-select-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(180px, 1fr);
  gap: 12px;
  align-items: center;
}

.member-picker-bar {
  grid-template-columns: auto minmax(0, 1fr);
  min-height: 42px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 6px 8px;
}

.member-picker-bar > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inline-link-button {
  border: 0;
  background: transparent;
  color: #1688ff;
  font-weight: 700;
  padding: 0;
}

.node-block-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.node-block-heading span,
.inline-link {
  color: #1688ff;
}

.side-checks--plain {
  border: 0;
  padding: 0;
}

.multi-level-subpanel {
  display: grid;
  gap: 12px;
  padding-left: 28px;
}

.node-inline-check {
  display: flex !important;
  align-items: center;
  gap: 8px !important;
}

.node-inline-check select {
  width: 210px;
}

.multi-level-directory-row {
  grid-template-columns: 190px minmax(0, 1fr) !important;
}

.node-number-input {
  min-height: 38px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 0 10px;
}

@media (max-width: 760px) {
  .assignee-option-card,
  .assignee-option-card--approval {
    grid-template-columns: 1fr;
    gap: 18px;
  }

  .assignee-option-card--approval .assignee-option-group {
    grid-template-columns: 1fr;
  }

  .assignee-option-card--approval .assignee-option-group,
  .assignee-option-card--approval .assignee-option-group:not(:first-child),
  .assignee-option-card--approval .assignee-option-group:not(:last-child) {
    border-right: 0;
    padding: 0;
  }

  .member-picker-bar,
  .role-add-bar,
  .node-select-row {
    grid-template-columns: 1fr;
  }
}

.side-checks {
  border: 1px solid #e5ebf3;
  border-radius: 6px;
  padding: 12px 10px 2px;
}

.side-checks label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.branch-editor {
  display: grid;
  gap: 12px;
}

.branch-editor--simple {
  min-height: 0;
}

.branch-default-note {
  margin: 28px 0 0;
  color: #9aa3ad;
  font-size: 16px;
}

.branch-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.branch-tabs button {
  min-height: 32px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  background: #fff;
  padding: 0 10px;
}

.branch-tabs button.active {
  border-color: #3b82f6;
  color: #1d4ed8;
  background: #eff6ff;
}

.branch-panel {
  display: grid;
  gap: 12px;
}

.branch-panel input,
.branch-panel select {
  min-height: 36px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 0 10px;
}

.branch-default-row {
  display: flex !important;
  align-items: center;
  gap: 8px !important;
}

.branch-conditions {
  display: grid;
  gap: 10px;
  border: 1px solid #e5ebf3;
  border-radius: 6px;
  padding: 12px;
}

.branch-condition-builder {
  gap: 14px;
  background: #fbfcfe;
}

.branch-condition-builder--simple {
  border: 0;
  background: #fff;
  padding: 24px 0 0;
  gap: 20px;
}

.branch-condition-group {
  display: grid;
  gap: 12px;
  border: 1px solid #e4e9f2;
  border-radius: 8px;
  background: #fff;
  padding: 0 14px 14px;
  overflow: hidden;
}

.branch-condition-group--simple {
  border: 0;
  border-radius: 0;
  background: transparent;
  padding: 0;
  overflow: visible;
  gap: 12px;
}

.branch-condition-group-head {
  min-height: 44px;
  margin: 0 -14px;
  padding: 0 14px;
  background: #f2f4f7;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.branch-condition-group-head strong {
  font-size: 14px;
  color: #1f2937;
}

.branch-group-separator,
.branch-row-connector {
  color: #6b7280;
  font-weight: 700;
}

.branch-row-connector {
  padding-left: 2px;
}

.branch-condition-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.branch-condition-row {
  display: grid;
  grid-template-columns: minmax(110px, 1.1fr) minmax(90px, 0.8fr) minmax(110px, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.branch-condition-row--builder {
  grid-template-columns: 132px minmax(360px, 1fr) 44px;
}

.branch-condition-row--simple {
  grid-template-columns: 126px minmax(360px, 1fr) 46px;
  gap: 16px;
  min-height: 38px;
  align-items: center;
}

.branch-field-select {
  width: 100%;
}

.branch-condition-label {
  min-height: 36px;
  display: flex;
  align-items: center;
  color: #1f2937;
  font-weight: 700;
}

.branch-condition-row--simple .branch-condition-label {
  min-height: 38px;
  font-size: 16px;
}

.condition-value-editor {
  min-width: 0;
}

.condition-scope-editor,
.condition-number-editor,
.condition-basic-editor {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
}

.condition-scope-editor {
  display: block;
}

.condition-scope-trigger {
  width: 100%;
  min-height: 36px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 4px 8px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  word-break: break-word;
}

.branch-condition-row--simple .condition-scope-trigger,
.branch-condition-row--simple .condition-number-editor input,
.branch-condition-row--simple .condition-number-editor select,
.branch-condition-row--simple .condition-basic-editor input,
.branch-condition-row--simple .condition-basic-editor select {
  min-height: 38px;
  font-size: 15px;
  padding-left: 12px;
  padding-right: 12px;
}

.condition-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  border-radius: 4px;
  background: #eef5ff;
  color: #1d4ed8;
  padding: 0 8px;
  font-size: 13px;
}

.condition-chip button {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 0;
}

.condition-between-inputs {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 8px;
  align-items: center;
}

.condition-checkbox-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
  gap: 10px 16px;
}

.condition-checkbox-grid label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
}

.branch-condition-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #6b7280;
  font-size: 13px;
}

.branch-condition-builder--simple .branch-condition-actions {
  margin-left: 0;
  gap: 14px;
  color: #8b95a1;
  font-size: 15px;
}

.branch-condition-builder--simple .branch-condition-actions .secondary-btn {
  min-width: 136px;
  height: 38px;
  justify-content: center;
  font-size: 15px;
  padding: 0 16px;
}

.primary-outline-btn {
  min-height: 36px;
  border: 1px solid #1683ff;
  border-radius: 6px;
  background: #fff;
  color: #1683ff;
  padding: 0 14px;
  cursor: pointer;
}

.branch-condition-builder--simple .primary-outline-btn {
  width: 152px;
  min-height: 39px;
  border-color: #1683ff;
  background: #1683ff;
  color: #fff;
  font-size: 15px;
  padding: 0 16px;
}

.branch-condition-help {
  margin: -2px 0 0;
  color: #64748b;
  font-size: 13px;
}

.branch-condition-builder--simple .branch-condition-help {
  margin: -6px 0 0;
  color: #5870a4;
  font-size: 15px;
}

.condition-delete-btn {
  border: 0;
  background: transparent;
  color: #1d64d8;
  cursor: pointer;
  font-size: 15px;
  padding: 0;
}

.icon-link-btn {
  border: 0;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}

.flow-dialog-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}

.simulate-btn {
  display: block;
  margin-left: auto;
}

.approval-preview-mask {
  padding: 24px;
  place-items: center;
}

.approval-preview-dialog {
  width: min(1180px, calc(100vw - 48px));
  max-height: min(820px, calc(100vh - 48px));
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 28px 80px rgba(15, 23, 42, 0.24);
  overflow: hidden;
}

.approval-preview-header {
  min-height: 76px;
  padding: 18px 58px 16px 24px;
  border-bottom: 1px solid #e6ebf2;
  position: relative;
}

.approval-preview-header h2 {
  margin: 0;
  color: #111827;
  font-size: 20px;
}

.approval-preview-header p {
  margin: 6px 0 0;
  color: #6b7280;
  font-size: 14px;
}

.approval-preview-layout {
  min-height: 0;
  display: grid;
  grid-template-columns: 250px minmax(360px, 1fr) 300px;
  background: #f4f6f8;
}

.approval-preview-sidebar,
.approval-preview-flow {
  min-width: 0;
  padding: 22px;
  background: #fff;
  overflow: auto;
}

.approval-preview-sidebar {
  border-right: 1px solid #e6ebf2;
}

.approval-preview-flow {
  border-left: 1px solid #e6ebf2;
}

.approval-preview-device-tabs {
  display: grid;
  gap: 8px;
}

.approval-preview-device-tabs button {
  min-height: 44px;
  border: 1px solid #dce3ed;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  padding: 0 12px;
  font-weight: 700;
}

.approval-preview-device-tabs button[aria-pressed='true'] {
  border-color: #168dff;
  color: #0f6dff;
  background: #eef6ff;
}

.approval-preview-device-tabs svg {
  width: 18px;
  height: 18px;
}

.approval-preview-stats {
  display: grid;
  gap: 16px;
  margin: 22px 0 0;
}

.approval-preview-stats div {
  display: grid;
  gap: 5px;
}

.approval-preview-stats dt {
  color: #8b95a1;
  font-size: 13px;
}

.approval-preview-stats dd {
  margin: 0;
  color: #1f2937;
  font-weight: 700;
  line-height: 1.5;
}

.approval-preview-canvas {
  min-width: 0;
  min-height: 0;
  padding: 28px;
  display: grid;
  place-items: start center;
  overflow: auto;
}

.approval-preview-form {
  width: min(100%, 620px);
  background: #fff;
  border: 1px solid #e1e7ef;
  border-radius: 8px;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.10);
  overflow: hidden;
}

.approval-preview-form--mobile {
  width: min(380px, 100%);
  border: 12px solid #fff;
  border-radius: 28px;
}

.approval-preview-form header {
  min-height: 76px;
  padding: 18px 22px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid #edf1f6;
}

.approval-preview-form header strong,
.approval-preview-form header small {
  display: block;
}

.approval-preview-form header strong {
  color: #111827;
  font-size: 18px;
}

.approval-preview-form header small {
  margin-top: 3px;
  color: #8b95a1;
  font-size: 13px;
}

.approval-preview-field-list {
  display: grid;
}

.approval-preview-field {
  min-height: 56px;
  padding: 14px 22px;
  display: grid;
  grid-template-columns: minmax(120px, 0.9fr) minmax(0, 1fr);
  align-items: center;
  gap: 18px;
  border-bottom: 1px solid #edf1f6;
}

.approval-preview-form--mobile .approval-preview-field {
  grid-template-columns: minmax(100px, 0.8fr) minmax(0, 1fr);
  padding: 14px 18px;
}

.approval-preview-field:last-child {
  border-bottom: 0;
}

.approval-preview-field--block,
.approval-preview-field--compound {
  align-items: start;
}

.approval-preview-field > span {
  color: #455164;
  font-weight: 600;
}

.approval-preview-field i {
  color: #e5484d;
  font-style: normal;
}

.approval-preview-field strong {
  min-width: 0;
  color: #9aa3ad;
  font-weight: 500;
  text-align: right;
  overflow-wrap: anywhere;
}

.approval-preview-empty {
  min-height: 180px;
  display: grid;
  place-items: center;
  color: #8b95a1;
}

.approval-preview-flow h3 {
  margin: 0 0 18px;
  color: #111827;
  font-size: 18px;
}

.approval-preview-flow-list {
  display: grid;
  justify-items: start;
}

.approval-preview-flow-step {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}

.approval-preview-flow-step > span {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: #eef6ff;
  color: #0f6dff;
  font-size: 13px;
  font-weight: 800;
}

.approval-preview-flow-step--start > span {
  background: #f0fdf4;
  color: #16a34a;
}

.approval-preview-flow-step--end > span {
  background: #f4f5f7;
  color: #667085;
}

.approval-preview-flow-step--condition > span {
  background: #eef2ff;
  color: #4f46e5;
}

.approval-preview-flow-step--branch > span {
  background: #ecfdf3;
  color: #079455;
}

.approval-preview-flow-step.is-matched > span {
  box-shadow: 0 0 0 3px rgba(22, 141, 255, 0.12);
}

.approval-preview-flow-step.is-matched strong {
  color: #0f6dff;
}

.approval-preview-flow-step strong,
.approval-preview-flow-step small {
  display: block;
}

.approval-preview-flow-step strong {
  color: #1f2937;
  font-size: 15px;
}

.approval-preview-flow-step small {
  margin-top: 3px;
  color: #8b95a1;
  font-size: 13px;
  line-height: 1.4;
}

.approval-preview-flow-line {
  width: 1px;
  height: 24px;
  margin-left: 17px;
  background: #d8dee8;
}

.approval-preview-footer {
  min-height: 68px;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  border-top: 1px solid #e6ebf2;
  background: #fff;
}

.simulate-dialog {
  width: 420px;
  padding: 34px 48px 42px;
  text-align: center;
}

.simulate-dialog--interactive {
  width: min(1120px, calc(100vw - 48px));
  max-height: min(780px, calc(100vh - 48px));
  padding: 28px 32px 32px;
  text-align: left;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  overflow: hidden;
}

.simulate-dialog--interactive h2 {
  margin: 0;
  color: #111827;
  font-size: 22px;
}

.simulate-subtitle {
  margin: 8px 0 22px;
  color: #667085;
  font-size: 14px;
}

.simulate-layout {
  min-height: 0;
  display: grid;
  grid-template-columns: 250px minmax(320px, 1fr) 310px;
  gap: 18px;
  overflow: hidden;
}

.simulate-panel,
.simulate-form,
.simulate-result {
  min-width: 0;
  min-height: 0;
  border: 1px solid #e1e7ef;
  border-radius: 8px;
  background: #fff;
  padding: 18px;
  overflow: auto;
}

.simulate-panel {
  display: grid;
  align-content: start;
  gap: 14px;
}

.simulate-panel label,
.simulate-field-list label {
  display: grid;
  gap: 7px;
}

.simulate-panel label > span,
.simulate-field-list label > span {
  color: #667085;
  font-size: 13px;
  font-weight: 700;
}

.simulate-panel select,
.simulate-field-list input,
.simulate-field-list select {
  min-height: 40px;
  border: 1px solid #d9e2ee;
  border-radius: 6px;
  background: #fff;
  color: #1f2937;
  padding: 0 10px;
  font-size: 14px;
}

.simulate-context {
  margin: 2px 0 0;
  color: #8b95a1;
  font-size: 13px;
  line-height: 1.5;
}

.simulate-form h3,
.simulate-result h3 {
  margin: 0 0 14px;
  color: #111827;
  font-size: 17px;
}

.simulate-field-list {
  display: grid;
  gap: 13px;
}

.simulate-field-list i {
  color: #e5484d;
  font-style: normal;
}

.simulate-result > p {
  margin: -4px 0 16px;
  color: #667085;
  font-size: 14px;
  line-height: 1.5;
}

.simulate-flow-list {
  gap: 0;
}

.rule-modal {
  width: 520px;
  max-height: 72vh;
  overflow: auto;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 16px 60px rgba(0, 0, 0, 0.18);
  padding: 26px 30px;
}

.rule-modal--member {
  width: min(980px, 94vw);
}

.rule-modal h2 {
  margin: 0 0 14px;
}

.dialog-options {
  display: grid;
  gap: 10px;
  max-height: 44vh;
  overflow: auto;
}

.dialog-options label {
  display: flex;
  align-items: center;
  gap: 10px;
}

.rule-member-picker .member-picker-layout {
  grid-template-columns: 240px minmax(260px, 1fr) 240px;
  min-height: 360px;
}

.modal-actions {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.member-picker-modal {
  width: min(980px, 94vw);
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 16px 60px rgba(0, 0, 0, 0.18);
  padding: 24px 24px 18px;
}

.member-picker-modal h2 {
  margin: 0 0 14px;
}

.role-picker-modal {
  width: min(720px, 92vw);
  max-height: min(720px, calc(100vh - 72px));
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 16px 60px rgba(0, 0, 0, 0.18);
  padding: 24px 24px 18px;
  overflow: auto;
}

.role-picker-mask {
  z-index: 120;
}

.role-picker-modal h2 {
  margin: 0 0 14px;
}

.role-picker-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 14px;
  min-height: 340px;
}

.role-picker-tree,
.role-picker-selected {
  border: 1px solid #dfe5ee;
  border-radius: 8px;
  padding: 12px;
  overflow: auto;
}

.role-picker-selected {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.role-selected-list {
  min-height: 38px;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.role-chip {
  max-width: 100%;
  display: inline-flex;
  align-items: center;
  border-radius: 4px;
  background: #eef6ff;
  color: #1d4ed8;
  padding: 6px 9px;
  font-size: 13px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.member-picker-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 14px;
  min-height: 420px;
}

.department-pane, .employee-pane, .selected-member-pane {
  border: 1px solid #dfe5ee;
  border-radius: 8px;
  padding: 12px;
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 10px;
}

.department-pane label, .employee-pane label {
  display: grid;
  gap: 6px;
  font-size: 14px;
}

.department-pane input, .employee-pane input {
  min-height: 36px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 0 10px;
}

.selected-member-pane strong {
  font-size: 14px;
}

.selected-member-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-content: flex-start;
  overflow: auto;
}

.selected-member-list span {
  max-width: 100%;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-radius: 4px;
  background: #eef6ff;
  color: #1d4ed8;
  font-size: 13px;
}

.selected-member-list button {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
}

.department-tree {
  overflow: auto;
  max-height: 320px;
}

.employee-list {
  border: 1px solid #eef2f7;
  border-radius: 6px;
  padding: 8px;
  max-height: 320px;
  overflow: auto;
  display: grid;
  gap: 8px;
  align-content: start;
}

.member-picker-hint {
  margin: -4px 0 14px;
  color: #5d6b82;
  font-size: 13px;
}

.employee-item {
  border: 1px solid #d8dee8;
  border-radius: 6px;
  background: #fff;
  text-align: left;
  padding: 9px 10px;
  display: grid;
  gap: 4px;
}

.employee-item--multiple {
  grid-template-columns: 22px 1fr;
  align-items: center;
}

.employee-item.active {
  border-color: #2f7de1;
  background: #f4f8ff;
}

.employee-check {
  width: 18px;
  height: 18px;
  border: 1px solid #b8c4d6;
  border-radius: 4px;
  color: #2f7de1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
}

.employee-item.active .employee-check {
  border-color: #2f7de1;
  background: #eaf2ff;
}

.employee-info {
  display: grid;
  gap: 4px;
}

.employee-item strong {
  font-size: 14px;
}

.employee-item small {
  color: #7a8697;
}

.condition-picker-modal {
  width: min(760px, 92vw);
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 16px 60px rgba(0, 0, 0, 0.18);
  padding: 34px 48px 22px;
}

.condition-picker-modal h2 {
  margin: 0 0 28px;
  color: #111827;
  font-size: 22px;
}

.condition-picker-modal p {
  margin: 0 0 28px;
  color: #1f2937;
  font-size: 18px;
  font-weight: 700;
}

.condition-picker-options {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 28px;
  min-height: 64px;
  padding-bottom: 20px;
}

.condition-picker-options label {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  color: #1f2937;
  font-size: 18px;
  font-weight: 700;
}

.condition-picker-options input {
  width: 18px;
  height: 18px;
}

.applicant-scope-modal {
  position: relative;
  width: min(1120px, 94vw);
  max-height: calc(100vh - 48px);
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 16px 60px rgba(0, 0, 0, 0.18);
  padding: 28px 32px 20px;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  overflow: hidden;
}

.applicant-scope-modal h2 {
  margin: 0 0 18px;
  color: #111827;
  font-size: 24px;
}

.scope-search {
  display: grid;
  gap: 6px;
  margin-bottom: 16px;
  font-size: 14px;
  color: #1f2937;
  font-weight: 600;
}

.scope-search input {
  min-height: 44px;
  border: 1px solid #d8dee8;
  border-radius: 6px;
  padding: 0 12px;
  font-size: 15px;
}

.scope-picker-layout {
  display: grid;
  grid-template-columns: minmax(560px, 1fr) 320px;
  gap: 18px;
  height: clamp(360px, calc(100vh - 300px), 520px);
  min-height: 0;
}

.scope-tree-pane,
.scope-selected-pane {
  border: 1px solid #dfe5ee;
  border-radius: 8px;
  background: #fff;
  overflow: auto;
  min-height: 0;
}

.scope-tree-pane {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
}

.scope-selected-pane {
  padding: 16px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}

.scope-tree-pane h3,
.scope-selected-pane h3 {
  margin: 0 0 14px;
  color: #1f2937;
  font-size: 17px;
  font-weight: 700;
}

.scope-mode-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 16px 18px 14px;
  border-bottom: 1px solid #edf0f4;
}

.scope-mode-tabs button {
  min-height: 76px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  color: #4b5563;
  display: grid;
  place-items: center;
  gap: 8px;
  cursor: pointer;
  font-weight: 600;
}

.scope-mode-tabs button.active {
  border-color: #1683ff;
  background: #eef5ff;
  color: #1677ff;
}

.scope-mode-tabs svg {
  width: 24px;
  height: 24px;
}

.scope-pane-body {
  padding: 16px 18px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
}

.scope-tree {
  min-height: 0;
  max-height: none;
  overflow: auto;
}

.scope-role-list {
  display: grid;
  gap: 10px;
  max-height: none;
  min-height: 0;
  overflow: auto;
}

.scope-role-item {
  min-height: 58px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 14px;
  color: #253044;
  text-align: left;
  cursor: pointer;
}

.scope-role-item.active {
  border-color: #1683ff;
  background: #eef5ff;
  color: #1677ff;
}

.scope-role-item svg {
  width: 22px;
  height: 22px;
  color: #1677ff;
}

.scope-role-item span {
  display: grid;
  gap: 2px;
}

.scope-role-item strong {
  font-size: 15px;
}

.scope-role-item small {
  color: #8b95a1;
}

.scope-selected-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-content: flex-start;
  min-height: 0;
  overflow: auto;
}

.applicant-scope-modal .modal-actions {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #edf0f4;
}

.empty-tip {
  margin: 0;
  color: #8b95a1;
  padding: 14px 8px;
}

.readonly-tip {
  margin: 0 0 14px;
}

@media (max-width: 1100px) {
  .feature-grid, .form-designer-shell, .filter-grid, .basic-settings {
    grid-template-columns: 1fr;
  }

  .approval-editor-topbar {
    height: auto;
    grid-template-columns: 1fr;
    align-items: start;
    gap: 12px;
    padding: 14px 18px;
  }

  .approval-editor-steps {
    width: 100%;
    min-width: 0;
    justify-content: flex-start;
    gap: 18px;
    overflow-x: auto;
    padding-bottom: 2px;
  }

  .approval-editor-steps button {
    height: 48px;
    flex: 0 0 auto;
    font-size: 16px;
  }

  .approval-editor-actions {
    justify-self: start;
    flex-wrap: wrap;
  }

  .control-workbench {
    grid-template-columns: 56px minmax(0, 1fr);
  }

  .control-palette,
  .designer-config-panel {
    max-height: none;
  }

  .form-designer-stage {
    min-height: 720px;
  }

  .approval-form-manager {
    padding: 22px 18px 32px;
  }

  .form-manager-sticky {
    margin: -22px -18px 0;
    padding: 22px 18px 18px;
  }

  .form-manager-header,
  .template-category-bar {
    grid-template-columns: 1fr;
  }

  .form-manager-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .form-search {
    width: 100%;
    min-width: 0;
  }

  .template-category-tabs {
    gap: 22px;
  }

  .group-sort-btn {
    width: max-content;
    border-left: 0;
    padding-left: 0;
  }

  .template-group-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .template-table-shell {
    overflow-x: auto;
  }

  .template-table {
    min-width: 920px;
  }

  .template-row {
    grid-template-columns: 1fr;
    gap: 8px;
    padding: 14px 28px;
  }

  .phone-preview {
    max-width: 430px;
  }

  .flow-setting-layout {
    grid-template-columns: 1fr;
  }

  .flow-designer-toolbar {
    display: grid;
  }

  .flow-workbench-actions {
    justify-content: flex-start;
  }

  .flow-config-drawer {
    width: min(440px, 94vw);
    max-width: 94vw;
  }

  .approval-preview-layout {
    grid-template-columns: 1fr;
  }

  .approval-preview-sidebar,
  .approval-preview-flow {
    border: 0;
    border-bottom: 1px solid #e6ebf2;
  }

  .approval-preview-device-tabs {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .simulate-dialog--interactive {
    width: min(760px, calc(100vw - 32px));
    max-height: calc(100vh - 32px);
  }

  .simulate-layout {
    grid-template-columns: 1fr;
    overflow: auto;
  }

  .basic-card {
    padding: 26px 20px 34px;
  }

  .basic-identity-row {
    grid-template-columns: 56px minmax(0, 1fr);
  }

  .basic-icon-picker,
  .basic-icon-picker .template-icon {
    width: 52px;
    height: 52px;
  }

  .member-picker-layout {
    grid-template-columns: 1fr;
  }

  .scope-picker-layout {
    grid-template-columns: 1fr;
  }

  .scope-tree,
  .scope-role-list {
    max-height: 320px;
  }
}
</style>
