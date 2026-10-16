# 电商销售日报与经营分析系统 (Ecom Daily Report) 工作区规则

## 一、依赖供应链安全与版本防投毒审计准则 (Supply Chain Security & Zero-Floating Invariant)

### 1. 审计优先级红线
- 凡接收到“全方位审计”、“代码审查”、“架构审查”或“安全检查”要求时，**必须将【第三方依赖供应链安全与版本锁定】列为第一梯队必查项**，严禁只审查手写的业务代码（如 SQL/AST 注入、权限控制等）而遗漏外部依赖。
- 审计报告必须设立《依赖供应链与第三方包安全》专门章节，显式报告直接依赖与间接依赖的锁定状态；若存在版本浮动或未锁死文件，必须作为高优先级缺陷报告。

### 2. 零浮动版本铁律 (Zero-Floating Invariant)
1. **前端 npm 依赖**：
   - `package.json` 中的 `dependencies` 与 `devDependencies` 严禁出现 `^`（兼容次版本）或 `~`（兼容补丁版本）等通配符，所有包必须锁死为经实测验证的具体版本号（如 `"vue": "3.5.42"`）。
   - 必须配置 `.npmrc` 并强制包含 `save-exact=true` 与 `package-lock=true`，封死后续任何误操作注入浮动版本的途径。
   - `package-lock.json`（或 `pnpm-lock.yaml`）必须完整纳入版本控制，严禁忽略或随意删除。
2. **后端 Python 依赖**：
   - 生产环境、交付包与便携版必须配备精确锁死清单（如 `requirements.lock`），对全部直接依赖与二级/三级间接依赖使用 `==` 全量精准钉死。
   - 便携版（如 `portable/core/requirements.txt`）严禁使用 `>=` 或 `<` 范围通配符，杜绝普通用户在本地运行批处理时被静默拉入未经测试的最新被篡改版本。

### 3. 依赖安装与运行保障
- 任何自动化批处理（`.bat` / `.sh`）、容器镜像或 CI/CD 构建脚本在执行依赖安装时，必须优先使用锁死文件（如 `npm ci` 代替 `npm install`；`pip install -r requirements.lock`）。
