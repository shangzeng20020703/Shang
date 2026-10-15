<template>
  <ion-page>
    <ion-content fullscreen class="workbench-landing">
      <main class="workbench-shell" aria-label="移动工作台">
        <header class="workbench-header">
          <div>
            <span>全部应用</span>
            <h1>工作台</h1>
          </div>
        </header>

        <label class="workbench-search" aria-label="搜索应用">
          <ion-icon :icon="searchOutline" />
          <input v-model.trim="serviceQuery" type="search" placeholder="搜索打卡、项目、审批、团队考勤" />
        </label>

        <div class="workbench-content">
          <section
            v-for="section in visibleSections"
            :key="section.title"
            class="workbench-section"
            :aria-label="section.title"
          >
            <div class="workbench-section__head">
              <div>
                <span>{{ section.kicker }}</span>
                <h2>{{ section.title }}</h2>
              </div>
              <em>{{ section.items.length }} 项</em>
            </div>
            <div class="workbench-grid">
              <button
                v-for="item in section.items"
                :key="item.title"
                class="workbench-app"
                type="button"
                :aria-label="item.title"
                :style="{ '--icon-bg': item.accent, '--icon-shadow': item.accent }"
                @click="openItem(item.path)"
              >
                <span
                  class="workbench-icon"
                  aria-hidden="true"
                  :class="`workbench-icon--${section.tone}`"
                >
                  <ion-icon :icon="item.icon" />
                </span>
                <span class="workbench-app__title">{{ item.title }}</span>
                <small>{{ item.caption }}</small>
              </button>
            </div>
          </section>

          <section v-if="!visibleSections.length" class="workbench-empty">
            没有匹配到相关应用
          </section>
        </div>
      </main>
    </ion-content>
  </ion-page>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { IonContent, IonIcon, IonPage } from '@ionic/vue'
import { searchOutline } from 'ionicons/icons'
import { isRoleAllowed } from '@/config/access'
import { workbenchSections } from '@/config/workbench'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const serviceQuery = ref('')

const visibleSections = computed(() => {
  const keyword = serviceQuery.value.trim().toLowerCase()
  return workbenchSections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        const matchesRole = isRoleAllowed(auth.roles, item.roles)
        const matchesKeyword = !keyword || `${item.title} ${item.caption}`.toLowerCase().includes(keyword)
        return matchesRole && matchesKeyword
      }),
    }))
    .filter((section) => section.items.length)
})

function openItem(path?: string) {
  if (!path) return
  void router.push(path)
}
</script>

<style scoped>
.workbench-landing {
  --workbench-page: #f7f9fc;
  --workbench-card: #ffffff;
  --workbench-border: rgba(214, 223, 235, 0.86);
  --workbench-text: #111827;
  --workbench-muted: #64748b;
  --background: var(--workbench-page);
  background-color: var(--workbench-page);
}

.workbench-shell {
  box-sizing: border-box;
  width: min(100%, var(--mobile-window-width));
  min-height: 100%;
  margin: 0 auto;
  padding: 0 14px calc(28px + env(safe-area-inset-bottom));
  background: linear-gradient(180deg, #eff5ff 0, #f7f9fc 136px, #f7f9fc 100%);
}

.workbench-header {
  position: sticky;
  top: 0;
  z-index: 2;
  min-height: calc(64px + env(safe-area-inset-top));
  padding-top: env(safe-area-inset-top);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: rgba(237, 244, 251, 0.96);
  border-bottom: 1px solid rgba(221, 229, 240, 0.72);
  backdrop-filter: blur(18px);
}

.workbench-header span {
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.workbench-header h1 {
  margin: 5px 0 0;
  color: var(--workbench-text);
  font-size: 23px;
  font-weight: 800;
  line-height: 1;
}

.workbench-search {
  min-height: 44px;
  margin-top: 12px;
  padding: 0 12px;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 9px;
  background: #ffffff;
  color: #64748b;
  box-shadow: 0 8px 22px rgba(15, 35, 63, 0.05);
}

.workbench-search ion-icon {
  font-size: 18px;
}

.workbench-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #111827;
  font: inherit;
  font-size: 14px;
}

.workbench-content {
  display: grid;
  gap: 12px;
  padding-top: 14px;
}

.workbench-section {
  padding: 16px 12px 18px;
  border: 1px solid var(--workbench-border);
  border-radius: 8px;
  background: var(--workbench-card);
  box-shadow: 0 8px 24px rgba(31, 42, 68, 0.055);
}

.workbench-section__head {
  margin-bottom: 16px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.workbench-section h2 {
  margin: 5px 0 0;
  color: var(--workbench-text);
  font-size: 17px;
  font-weight: 800;
  line-height: 1.1;
}

.workbench-section__head span,
.workbench-section__head em {
  color: var(--workbench-muted);
  font-size: 12px;
  font-style: normal;
  font-weight: 800;
}

.workbench-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  column-gap: 10px;
  row-gap: 26px;
}

.workbench-app {
  min-width: 0;
  min-height: 112px;
  padding: 0 2px;
  border: 0;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  gap: 7px;
  background: transparent;
  color: #1f2937;
  text-align: center;
  -webkit-tap-highlight-color: transparent;
  transition:
    transform 120ms ease,
    background-color 120ms ease;
}

.workbench-app:active {
  transform: scale(0.96);
  background-color: rgba(226, 232, 240, 0.5);
}

.workbench-app:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.72);
  outline-offset: 3px;
}

.workbench-icon {
  width: 46px;
  height: 46px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  color: #ffffff;
  font-size: 24px;
  background: color-mix(in srgb, var(--icon-bg) 13%, #ffffff);
  color: var(--icon-bg);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.24),
    0 5px 12px color-mix(in srgb, var(--icon-shadow) 12%, transparent);
}

.workbench-icon--management {
  background: color-mix(in srgb, var(--icon-bg) 13%, #ffffff);
}

.workbench-icon--tools {
  background: color-mix(in srgb, var(--icon-bg) 13%, #ffffff);
}

.workbench-app__title {
  width: 100%;
  max-width: 72px;
  min-height: 18px;
  color: #202734;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.16;
  display: -webkit-box;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.workbench-app small {
  width: 100%;
  color: #64748b;
  font-size: 11px;
  line-height: 1.25;
  display: -webkit-box;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.workbench-empty {
  padding: 22px 12px;
  border: 1px solid var(--workbench-border);
  border-radius: 8px;
  background: #ffffff;
  color: #64748b;
  font-size: 13px;
  text-align: center;
}

@media (max-width: 360px) {
  .workbench-shell {
    padding-inline: 8px;
  }

  .workbench-grid {
    column-gap: 6px;
    row-gap: 24px;
  }

  .workbench-icon {
    width: 42px;
    height: 42px;
  }

  .workbench-app__title {
    max-width: 68px;
    font-size: 14px;
  }
}
</style>
