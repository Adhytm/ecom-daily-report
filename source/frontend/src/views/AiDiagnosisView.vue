<script setup>
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, toastError } from '../api'
import { useReportStore } from '../stores/report'

const report = useReportStore()

const provider = ref(localStorage.getItem('ecom_ai_provider') || 'local')
const localBaseUrl = ref(localStorage.getItem('ecom_ai_local_url') || 'http://localhost:11434')
const localModel = ref(localStorage.getItem('ecom_ai_local_model') || 'qwen2.5:7b')

const cloudBaseUrl = ref(localStorage.getItem('ecom_ai_cloud_url') || 'https://api.deepseek.com/v1')
// API Key 只存 sessionStorage（关窗即清），不落 localStorage 长期保存
const cloudKey = ref(sessionStorage.getItem('ecom_ai_cloud_key') || '')
const cloudModel = ref(localStorage.getItem('ecom_ai_cloud_model') || 'deepseek-chat')

const customPrompt = ref('')
const diagnosing = ref(false)
const diagnosisResult = ref(null)
const showAdvancedConfig = ref(false)

onMounted(async () => {
  await report.initDate()
})

watch(provider, (v) => localStorage.setItem('ecom_ai_provider', v))
watch(localBaseUrl, (v) => localStorage.setItem('ecom_ai_local_url', v))
watch(localModel, (v) => localStorage.setItem('ecom_ai_local_model', v))
watch(cloudBaseUrl, (v) => localStorage.setItem('ecom_ai_cloud_url', v))
watch(cloudKey, (v) => sessionStorage.setItem('ecom_ai_cloud_key', v))
watch(cloudModel, (v) => localStorage.setItem('ecom_ai_cloud_model', v))

async function runDiagnose() {
  if (provider.value === 'cloud' && !cloudKey.value) {
    ElMessage.warning('云端 API 模式必须填写 API Key')
    return
  }
  diagnosing.value = true
  diagnosisResult.value = null
  try {
    const payload = {
      date: report.reportDate,
      provider: provider.value,
      base_url: provider.value === 'local' ? localBaseUrl.value : cloudBaseUrl.value,
      api_key: provider.value === 'cloud' ? cloudKey.value : undefined,
      model: provider.value === 'local' ? localModel.value : cloudModel.value,
      custom_prompt: customPrompt.value || undefined,
    }
    const res = await api.ai.diagnose(payload)
    diagnosisResult.value = res
    ElMessage.success('AI 商业诊断完成')
  } catch (e) {
    if (e?.code === 'NETWORK_ERROR' && /timeout/i.test(e?.message || '')) {
      ElMessage.error('模型响应超时：本地大模型推理较慢，请确认服务已启动并选用更小的模型后重试')
    } else {
      toastError(e)
    }
  } finally {
    diagnosing.value = false
  }
}

async function copyDiagnosis() {
  if (!diagnosisResult.value?.diagnosis_markdown) return
  try {
    await navigator.clipboard.writeText(diagnosisResult.value.diagnosis_markdown)
    ElMessage.success('诊断报告已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选取复制')
  }
}
</script>

<template>
  <div class="ai-view page-container">
    <!-- 商业数据安全与隐私合规警示卡片 -->
    <el-card shadow="never" class="alert-card">
      <div class="alert-box">
        <div class="alert-icon">⚠️</div>
        <div class="alert-text">
          <div class="alert-title">商业数据安全与隐私防泄露警示</div>
          <p class="alert-desc">
            电商销售数据（GMV流水、真实到手毛利、进货成本、爆款名称、ROI）属于企业至关重要的核心商业机密。<br />
            • <strong>本地离线 AI（推荐 · 100% 隐私安全）</strong>：数据完全在您的本地电脑硬件（显卡/CPU）计算，全程断网，无任何外泄风险。<br />
            • <strong>商业公有云 API（存在数据泄露与训练滥用风险）</strong>：需调用公有云接口传输原始明细。
            <span class="highlight">目前行业内已有多家商业 AI 厂商在用户服务条款中默认或隐蔽将 API 请求数据截留用于大模型训练。</span>
            如涉及高敏感财务与供应链数据，请务必优先使用「本地离线 AI」模式！
          </p>
        </div>
      </div>
    </el-card>

    <!-- 配置面板 -->
    <el-card shadow="never" class="config-card">
      <template #header>
        <div class="card-head">
          <span class="title">⚡ AI 业务诊断配置</span>
          <div class="date-pick">
            <span class="label">诊断日期：</span>
            <el-date-picker
              v-model="report.reportDate"
              type="date"
              value-format="YYYY-MM-DD"
              :clearable="false"
              size="small"
              style="width: 140px"
            />
          </div>
        </div>
      </template>

      <el-form label-position="top">
        <div class="engine-summary-bar">
          <div class="engine-mode-tag">
            <el-radio-group v-model="provider" size="small">
              <el-radio-button value="local">
                🔒 本地离线 AI (100% 隐私安全)
              </el-radio-button>
              <el-radio-button value="cloud">
                ☁️ 商业公有云 API
              </el-radio-button>
            </el-radio-group>
            <span class="active-model-hint">
              当前模型：<b>{{ provider === 'local' ? localModel : cloudModel }}</b>
            </span>
          </div>

          <el-button
            link
            type="primary"
            size="small"
            @click="showAdvancedConfig = !showAdvancedConfig"
          >
            {{ showAdvancedConfig ? '▲ 收起高级参数配置' : '▼ 展开接口与模型配置' }}
          </el-button>
        </div>

        <el-collapse-transition>
          <div v-show="showAdvancedConfig" class="advanced-config-panel">
            <!-- 本地配置 -->
            <div v-if="provider === 'local'" class="local-box">
              <div class="box-tip">
                💡 本地模式连接您电脑上的 Ollama 或 LM Studio 服务，数据绝不上传云端。
              </div>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="本地服务地址 (Base URL)">
                    <el-input v-model="localBaseUrl" placeholder="http://localhost:11434" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="本地模型名称">
                    <el-input v-model="localModel" placeholder="qwen2.5:7b 或 deepseek-r1:8b" />
                  </el-form-item>
                </el-col>
              </el-row>
            </div>

            <!-- 云端配置 -->
            <div v-else class="cloud-box">
              <div class="box-warn">
                ⚠️ 您正在启用公有云 API 模式，数据将通过公网加密传输至该服务商。API Key 仅保存在您本地浏览器中。
              </div>
              <el-row :gutter="16">
                <el-col :span="8">
                  <el-form-item label="云端服务地址 (Base URL)">
                    <el-input v-model="cloudBaseUrl" placeholder="https://api.deepseek.com/v1" />
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item label="API Key">
                    <el-input v-model="cloudKey" type="password" show-password placeholder="sk-..." />
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item label="模型名称">
                    <el-input v-model="cloudModel" placeholder="deepseek-chat 或 qwen-plus" />
                  </el-form-item>
                </el-col>
              </el-row>
            </div>
          </div>
        </el-collapse-transition>

        <el-form-item label="补充诊断关注点（可选）：" style="margin-top: 14px">
          <el-input
            v-model="customPrompt"
            type="textarea"
            :rows="2"
            placeholder="例如：重点帮我排查抖店今天退款率为什么飙高、分析直通车投流是否划算、爆款 A 是否面临断货降权风险"
          />
        </el-form-item>

        <div class="action-row">
          <el-button
            type="primary"
            size="large"
            :loading="diagnosing"
            @click="runDiagnose"
          >
            ⚡ 开始智能商业诊断与归因分析
          </el-button>
        </div>
      </el-form>
    </el-card>

    <!-- 诊断结果卡片 -->
    <el-card v-if="diagnosisResult" shadow="never" class="result-card">
      <template #header>
        <div class="res-head">
          <span>📋 【{{ diagnosisResult.date }}】商业经营智能诊断报告</span>
          <div class="tools">
            <el-tag size="small" type="info">{{ diagnosisResult.model }} ({{ diagnosisResult.provider }})</el-tag>
            <el-button size="small" type="primary" plain @click="copyDiagnosis">复制完整诊断</el-button>
          </div>
        </div>
      </template>

      <div class="diagnosis-body">
        <pre class="md-content">{{ diagnosisResult.diagnosis_markdown }}</pre>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.ai-view {
  width: 100%;
}
.alert-card {
  border-left: 4px solid var(--el-color-danger);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  background: var(--el-color-danger-light-9);
}
:global(html.dark) .alert-card {
  background: rgba(245, 108, 108, 0.12);
}
.alert-box {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.alert-icon {
  font-size: 26px;
  line-height: 1;
}
.alert-title {
  font-size: var(--text-lg);
  font-weight: 700;
  color: var(--el-color-danger);
  margin-bottom: 6px;
}
.alert-desc {
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
  line-height: 1.7;
  margin: 0;
}
.alert-desc .highlight {
  color: var(--color-up);
  font-weight: 600;
}
:global(html.dark) .alert-desc .highlight {
  color: var(--color-up);
}

.config-card, .result-card {
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-head .title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--el-color-primary);
}
.date-pick {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}

.engine-summary-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  background: var(--el-fill-color-light);
  border-radius: var(--radius-md);
  padding: 12px 16px;
}
.engine-mode-tag {
  display: flex;
  align-items: center;
  gap: 12px;
}
.active-model-hint {
  font-size: var(--text-sm);
  color: var(--el-text-color-regular);
}
.active-model-hint b {
  color: var(--el-color-primary);
}
.advanced-config-panel {
  margin-top: 8px;
}

.local-box, .cloud-box {
  border-radius: var(--radius-md);
  padding: 14px 16px;
  margin-top: 10px;
}
.local-box {
  background: var(--el-color-success-light-9);
  border: 1px solid var(--el-color-success-light-8);
}
.box-tip {
  font-size: var(--text-sm);
  color: var(--el-color-success);
  font-weight: 500;
  margin-bottom: 10px;
}
.cloud-box {
  background: var(--el-color-warning-light-9);
  border: 1px solid var(--el-color-warning-light-8);
}
.box-warn {
  font-size: var(--text-sm);
  color: var(--el-color-warning);
  font-weight: 500;
  margin-bottom: 10px;
}
:global(html.dark) .local-box {
  background: rgba(103, 194, 58, 0.15);
  border-color: rgba(103, 194, 58, 0.3);
}
:global(html.dark) .cloud-box {
  background: rgba(230, 162, 60, 0.15);
  border-color: rgba(230, 162, 60, 0.3);
}

.action-row {
  margin-top: 12px;
  text-align: center;
}
.res-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--el-color-primary);
}
.res-head .tools {
  display: flex;
  gap: 8px;
  align-items: center;
}
.diagnosis-body {
  padding: 8px 4px;
}
.md-content {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: var(--text-md);
  line-height: 1.8;
  color: var(--el-text-color-primary);
  white-space: pre-wrap;
  word-wrap: break-word;
  background: var(--el-fill-color-light);
  padding: 20px;
  border-radius: var(--radius-md);
  border: 1px solid var(--el-border-color-lighter);
}
</style>
