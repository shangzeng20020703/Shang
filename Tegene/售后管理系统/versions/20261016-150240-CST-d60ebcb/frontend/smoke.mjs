import {chromium} from '@playwright/test'
import fs from 'node:fs'
import {fileURLToPath} from 'node:url'
process.chdir(fileURLToPath(new URL('.',import.meta.url)))
fs.mkdirSync('../evidence/screenshots',{recursive:true})
const browser=await chromium.launch({headless:true,channel:'chrome'})
const result={pages:[],errors:[],failedRequests:[]}
const page=await browser.newPage({viewport:{width:1440,height:1000}})
page.on('pageerror',e=>result.errors.push(e.message))
page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)result.failedRequests.push([r.status(),r.url()])})
await page.goto('http://127.0.0.1:5186/login')
await page.getByPlaceholder('请输入账号或手机号').fill('19900000001')
await page.getByPlaceholder('请输入密码',{exact:true}).fill('Demo-AfterSales-2026!')
await page.getByRole('button',{name:/登\s*录/,exact:true}).click()
await page.waitForURL('**/dashboard')
for(const path of ['dashboard','field','employees','attendance','approval','leave','organization','event-monitor','audit']){
 await page.goto('http://127.0.0.1:5186/'+path);await page.waitForLoadState('networkidle');
 result.pages.push({path,title:await page.title(),text:(await page.locator('body').innerText()).slice(0,1500)})
 if(path==='approval'){
  await page.getByRole('button',{name:'编辑',exact:true}).first().click();
  await page.getByRole('button',{name:'保存草稿',exact:true}).click();
  await page.getByText('草稿已保存，已发布版本保持不变',{exact:true}).waitFor();
  await page.reload();await page.waitForLoadState('networkidle');
  await page.getByRole('button',{name:'编辑',exact:true}).first().click();
  await page.getByRole('button',{name:'继续草稿',exact:true}).waitFor();
  result.pages.push({path:'template/draft-roundtrip',persisted:true});
  await page.goto('http://127.0.0.1:5186/approval');await page.waitForLoadState('networkidle');
 }
 if(['dashboard','field','approval'].includes(path))await page.screenshot({path:`../evidence/screenshots/desktop-${path}.png`,fullPage:true})
}
const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,permissions:['geolocation'],geolocation:{latitude:31.2304,longitude:121.4737}})
const mp=await mobile.newPage();mp.on('pageerror',e=>result.errors.push('mobile: '+e.message));mp.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)result.failedRequests.push([r.status(),r.url()])})
await mp.goto('http://127.0.0.1:5187/mobile/login');await mp.waitForLoadState('networkidle')
await mp.locator('input').first().fill('19900000003');await mp.locator('input[type=password]').fill('Demo-AfterSales-2026!');await mp.getByRole('button',{name:'登录移动端',exact:true}).click();await mp.waitForURL('**/app/tabs/home')
for(const path of ['tabs/home','field','tabs/approvals','approval/start','notifications','tabs/profile','attendance']){
 await mp.goto('http://127.0.0.1:5187/mobile/app/'+path);await mp.waitForLoadState('networkidle');
 result.pages.push({path:'mobile/'+path,text:(await mp.locator('body').innerText()).slice(0,1200)})
 if(path==='attendance'){
   mp.on('dialog',d=>d.accept());
   const punch=mp.locator('.punch-circle-btn');
   if((await punch.innerText()).includes('上班打卡')){
     const response=mp.waitForResponse(r=>r.url().endsWith('/attendance/clock-in'),{timeout:20000});
     await punch.click();const r=await response;if(r.status()!==200)throw new Error('Mobile clock-in failed: '+await r.text());
     result.pages.push({path:'mobile/punch-action',status:r.status()});
     await mp.waitForLoadState('networkidle');
   }
 }
 if(path==='attendance')await mp.screenshot({path:'../evidence/screenshots/mobile-attendance.png',fullPage:true});
 if(path==='tabs/home')await mp.screenshot({path:'../evidence/screenshots/mobile-home.png',fullPage:true})
}
const manager=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const gp=await manager.newPage();gp.on('pageerror',e=>result.errors.push('manager: '+e.message));
await gp.goto('http://127.0.0.1:5187/mobile/login');await gp.locator('input').first().fill('19900000002');await gp.locator('input[type=password]').fill('Demo-AfterSales-2026!');await gp.getByRole('button',{name:'登录移动端',exact:true}).click();await gp.waitForURL('**/app/tabs/home');
for(const path of ['team-attendance','field']){await gp.goto('http://127.0.0.1:5187/mobile/app/'+path);await gp.waitForLoadState('networkidle');result.pages.push({path:'manager/'+path,text:(await gp.locator('body').innerText()).slice(0,1000)});}
fs.writeFileSync('../evidence/browser-smoke.json',JSON.stringify(result,null,2));await browser.close();console.log(JSON.stringify(result,null,2))
if(result.errors.length||result.failedRequests.length)process.exitCode=1
