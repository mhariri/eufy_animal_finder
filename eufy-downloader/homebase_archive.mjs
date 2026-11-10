#!/usr/bin/env node
/** Read-only HomeBase 3 archive investigation. Private data stays in the ignored state directory. */
import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { decodeHomebaseFrames } from './decode_homebase.mjs';
import { EufyMega, FileSessionStore, LoginStatus, p2pCodec, VideoFrameDecoder, AccessUnitAssembler, parseVideoFrameHeader } from '@mega-yfue/eufy-sdk';
const dir=`${import.meta.dirname}/.eufy-state`;
const c=JSON.parse(fs.readFileSync(`${dir}/credentials.json`));
const client=new EufyMega({email:c.username,password:c.password,countryCode:c.country,autoRealtime:false,store:new FileSessionStore(`${dir}/mega-session.json`)});
client.on('error',e=>console.error(e.message));
function save(name,data){fs.writeFileSync(`${dir}/${name}.json`,JSON.stringify(data,null,2),{mode:0o600});}
function parseObject(s){
 const start=s.indexOf('{');if(start<0)return;
 let depth=0,quoted=false,escape=false;
 for(let i=start;i<s.length;i++){
  const ch=s[i];if(quoted){if(escape)escape=false;else if(ch==='\\')escape=true;else if(ch==='"')quoted=false;}
  else if(ch==='"')quoted=true;else if(ch==='{')depth++;else if(ch==='}'&&--depth===0){try{return JSON.parse(s.slice(start,i+1));}catch{return;}}
 }
}
async function query(session,admin,cmd,payload){
 const transaction=String(Date.now());
 return await new Promise((resolve,reject)=>{
  let text='';
  function finish(error,result){clearTimeout(timer);session.off('dbChunk',chunk);error?reject(error):resolve(result);}
  function chunk(c){text+=c.text;const j=parseObject(Buffer.from(text,'latin1').toString('utf8'));if(!j)return;if(j.cmd!==cmd||String(j.transaction)!==transaction){text='';return;}finish(j.mIntRet!==0?new Error(`Database refused: ${j.mIntRet}`):null,j);}
  const timer=setTimeout(()=>finish(new Error('Database response timeout')),30000);
  session.on('dbChunk',chunk);
  if(!session.sendRawLevel2(JSON.stringify({account_id:admin,cmd:1306,mChannel:255,mValue3:0,payload:{table:'history_record_info',cmd,payload,transaction}}),255))finish(new Error('GCM request failed'));
 });
}
function receive(session,request,channel=1,traceFile=null){
 return new Promise((resolve,reject)=>{
  const frames=[];let bytes=0,settled=false,mediaChannel;const control=[];const observedMedia={};
  const timer=setTimeout(()=>finish(new Error('Transfer did not finish within 90 seconds')),90000);
  function finish(error){if(settled)return;settled=true;clearTimeout(timer);session.off('data',onData);session.off('close',onClose);if(traceFile){fs.writeFileSync(traceFile+'.media-summary.json',JSON.stringify(observedMedia),{mode:0o600});fs.writeFileSync(traceFile+'.control.json',JSON.stringify(control),{mode:0o600});}if(traceFile)fs.writeFileSync(traceFile,JSON.stringify(frames.map(f=>({...f,raw:f.raw.toString('base64')}))),{mode:0o600});error?reject(error):resolve(frames);}
  function onData(f){
   if(f.dataType===2&&[1024,1350].includes(f.commandId)&&[0,channel,255].includes(f.channel)&&Buffer.isBuffer(f.data)&&f.data.length===4){
    const resultCode=f.data.readInt32LE(0);
    control.push({cmd:f.commandId,channel:f.channel,resultCode});
    if(resultCode<0)return finish(new Error(`Archive request refused (${resultCode})`));
   }
   if([1300,1301].includes(f.commandId)){
    const shape=`${f.commandId}:${f.dataType}:${f.channel}`;
    observedMedia[shape]=(observedMedia[shape]??0)+1;
   }
   if([1303,1304].includes(f.commandId))control.push({cmd:f.commandId,channel:f.channel,signCode:f.signCode,dataType:f.dataType,body:Buffer.isBuffer(f.data)?f.data.toString('hex'):null});
   if(f.commandId===1304&&f.dataType===2&&[0,channel,255].includes(f.channel))return finish();

   // This exclusive session has one archive request. Historical files retain
   // their original media channel, independent of the current camera channel.
   if(f.dataType!==3||![1300,1301].includes(f.commandId))return;
   if(mediaChannel===undefined)mediaChannel=f.channel;
   if(f.channel!==mediaChannel)return finish(new Error('Mixed archive media channels'));
   frames.push({commandId:f.commandId,signCode:f.signCode,channel:f.channel,raw:Buffer.from(f.raw)});bytes+=f.raw.length;
   if(bytes>64*1024*1024)finish(new Error('Transfer exceeded investigation limit'));
  }
  function onClose(){finish(new Error('Station session closed during transfer'));}
  session.on('data',onData);session.once('close',onClose);
  const sealed=session.encryptLevel2(Buffer.from(JSON.stringify(request)));
  if(!sealed)return finish(new Error('GCM key unavailable'));
  const data=Buffer.concat([p2pCodec.buildCommandHeader(session.seqNumber,1350),p2pCodec.buildRawCommandPayload(sealed,channel,8,[8,0],13)]);
  session.seqNumber=(session.seqNumber+1)&0xffff;
  session.send(session.connectAddress,p2pCodec.RequestMessageType.DATA,data);
 });
}
try{
 const login=await client.login();if(login.status!==LoginStatus.Ok)throw new Error(`Login requires ${login.status}`);
 const devices=await client.getDevices();const station=devices.find(d=>process.env.EUFY_STATION_SN ? d.sn===process.env.EUFY_STATION_SN : d.model==='T8030'||d.sn.startsWith('T8030'));
 if(!station)throw new Error('HomeBase 3 not found');
 const admin=station.raw.member.admin_user_id;
 await client.connectStation(station.sn,AbortSignal.timeout(45000));
 const session=client.getP2pSessions().get(station.sn);
 if(!await session.awaitLevel2Key(20000,'call','every'))throw new Error('GCM session unavailable');
 const mode=process.argv[2]??'inventory';
 if(mode==='inventory'){
  const reply=await query(session,admin,10008,{start_date:process.env.EUFY_FROM_DATE??'20150101',end_date:process.env.EUFY_TO_DATE??new Date(Date.now()+86400000).toISOString().slice(0,10).replaceAll('-','')});
  save('archive-calendar',reply);console.log('Calendar:',Array.isArray(reply.data)?reply.data.length:0,'day entries');
 }else if(mode==='inventory-records'){
  const calendar=JSON.parse(fs.readFileSync(`${dir}/archive-calendar.json`));
  const days=[...new Set(calendar.data.map(r=>r.days))].sort().reverse();
  const root=`${dir}/archive-days`;fs.mkdirSync(root,{recursive:true,mode:0o700});
  save('archive-devices',devices.map(d=>({sn:d.sn,stationSn:d.stationSn,model:d.model,channel:d.channel,rawChannel:d.raw?.device_channel})));
  let complete=0,rows=0,bytes=0,failures=0;
  for(const day of days){
   if(!/^\d{8}$/.test(day))throw new Error('Unexpected calendar date');
   const file=`${root}/${day}.json`;let report;
   try{
    if(fs.existsSync(file))report=JSON.parse(fs.readFileSync(file));
    else {
     const next=new Date(Date.UTC(+day.slice(0,4),+day.slice(4,6)-1,+day.slice(6,8)+1)).toISOString().slice(0,10).replaceAll('-','');
     const reply=await query(session,admin,10011,{count:2000,end_date:next,end_id:1,flag:0,need_ai:1,res_unzip:1,start_date:day,start_id:0,update_time:'0',start_time:'0',alarm_id:''});
     const records=Array.isArray(reply.data)?reply.data.find(g=>g.table_name==='history_record_info')?.payload??[]:[];
     if(records.some(r=>r.station_sn!==station.sn))throw new Error('Unexpected station in recording rows');
     report={day,requestCount:2000,cursorStart:reply.start_id,cursorEnd:reply.end_id,possiblyTruncated:records.length>=2000,records};
     fs.writeFileSync(file,JSON.stringify(report),{mode:0o600});
    }
    complete++;rows+=report.records.length;bytes+=report.records.reduce((n,r)=>n+(Number(r.folder_size)||0),0);
   }catch(error){failures++;fs.appendFileSync(`${dir}/archive-inventory-errors.jsonl`,JSON.stringify({day,error:error.message})+'\n',{mode:0o600});}
   save('archive-inventory-progress',{totalDays:days.length,processedDays:complete,failedDays:failures,recordRows:rows,reportedFolderBytes:bytes,updatedAt:new Date().toISOString()});
   if((complete+failures)%10===0)console.log('Inventory progress:',complete,'/',days.length,'days;',rows,'recordings;',failures,'failed days');
  }
  console.log('Inventory finished:',complete,'days;',rows,'rows;',failures,'failed days');
 }else if(mode==='download-batch'||mode==='decode-local'){
  const root=path.join(dir,'recordings');fs.mkdirSync(root,{recursive:true,mode:0o700});
  const files=fs.readdirSync(`${dir}/archive-days`).filter(n=>n.endsWith('.json')).sort().reverse();
  let rows=files.flatMap(f=>JSON.parse(fs.readFileSync(`${dir}/archive-days/${f}`)).records);
  rows=rows.filter(r=>r.storage_path);
  if(process.env.EUFY_REVIEW_DAY)rows=rows.filter(r=>r.start_time.startsWith(process.env.EUFY_REVIEW_DAY));
  if(process.env.EUFY_REVIEW_TRIGGER)rows=rows.filter(r=>String(r.trigger_type)===process.env.EUFY_REVIEW_TRIGGER);
  if(process.env.EUFY_PRIORITY_ANIMALS==='1'){const priority=new Map([[64,0],[2,1],[128,2],[1,3],[0,4],[4,5]]);rows.sort((a,b)=>(priority.get(Number(a.trigger_type))??6)-(priority.get(Number(b.trigger_type))??6));}
  const limit=Number(process.argv[3]??16);let downloaded=0,failed=0;
  for(const row of rows){
   if(downloaded+failed>=limit)break;
   const id=crypto.createHash('sha256').update(row.storage_path).digest('hex').slice(0,20);
   if(process.argv[4]&&process.argv[4]!==id)continue;
   if(row.station_sn!==station.sn)throw new Error('Archive row belongs to another station');
   const folder=`${root}/${id}`;fs.mkdirSync(folder,{recursive:true,mode:0o700});
   if(fs.existsSync(`${folder}/metadata.json`))continue;
   const errorPath=`${folder}/error.json`;
   const priorError=fs.existsSync(errorPath)?JSON.parse(fs.readFileSync(errorPath)):null;
   const attemptLimit=Number(process.env.EUFY_ATTEMPT_LIMIT??3);
   if(priorError?.attempts>=attemptLimit)continue;
   const camera=devices.find(d=>d.sn===row.device_sn);
   // Removed-camera files remain accessible by original path on station channel 0.
   const channel=camera?.channel??0;
   if(!Number.isInteger(channel)){console.log('Skipping row with no authoritative camera channel');continue;}
   try{
    const ecc=crypto.createECDH('prime256v1');if(mode==='decode-local')ecc.setPrivateKey(fs.readFileSync(`${folder}/transfer-private-key`));else ecc.generateKeys();
    const scalar=ecc.getPrivateKey();
    const privateKey=Buffer.concat([Buffer.alloc(32-scalar.length),scalar]);const key=ecc.getPublicKey('hex','uncompressed').slice(2);
    fs.writeFileSync(`${folder}/transfer-private-key`,privateKey,{mode:0o600});
    const frames=mode==='decode-local'?JSON.parse(fs.readFileSync(`${folder}/transfer-frames.json`)).map(f=>({...f,raw:Buffer.from(f.raw,'base64')})):await receive(session,{account_id:admin,cmd:1024,mChannel:channel,mValue3:1024,payload:{filepath:row.storage_path,key}},channel,`${folder}/transfer-frames.json`);
    const decoded=decodeHomebaseFrames(frames,privateKey,Number(row.frame_num)||0);
    const {fps,videoFrames,wireVideoFrames,authenticated,audioFrames}=decoded;
    const raw=`${folder}/video.h264`;fs.writeFileSync(raw,decoded.video,{mode:0o600});
    const audioPath=`${folder}/audio.aac`;if(decoded.audio.length)fs.writeFileSync(audioPath,decoded.audio,{mode:0o600});
    const args=['-nostdin','-hide_banner','-loglevel','error','-threads','2','-r',String(fps),'-i',raw,...(decoded.audio.length?['-i',audioPath]:[]),'-c','copy','-movflags','+faststart','-y',`${folder}/video.mp4`];
    const mux=spawnSync('ffmpeg',args,{encoding:'utf8'});if(mux.status!==0)throw new Error('Video mux failed: '+mux.stderr.slice(0,200));
    const probe=spawnSync('ffprobe',['-v','error','-select_streams','v:0','-show_entries','stream=nb_frames','-of','json',`${folder}/video.mp4`],{encoding:'utf8'});
    if(probe.status!==0||Number(JSON.parse(probe.stdout).streams?.[0]?.nb_frames)!==videoFrames)throw new Error('Muxed video picture count mismatch');
    const sampleIndices=[...new Set([0.1,0.5,0.9].map(f=>Math.min(videoFrames-1,Math.floor(videoFrames*f))))];
    const selection=sampleIndices.map(n=>`eq(n,${n})`).join('+');
    // Keep the select counter across mid-clip resolution changes.
    const verify=spawnSync('ffmpeg',['-nostdin','-v','error','-threads','2','-reinit_filter','0','-i',`${folder}/video.mp4`,'-vf',`select='${selection}',scale='min(1280,iw)':-2`,'-fps_mode','vfr','-q:v','2','-y',`${folder}/sample-%02d.jpg`],{encoding:'utf8'});
    if(verify.status!==0||verify.stderr.trim())throw new Error('Video decode check failed');
    const sampleFrames=sampleIndices.map((frameIndex,i)=>({file:`sample-${String(i+1).padStart(2,'0')}.jpg`,frameIndex,seconds:frameIndex/fps}));
    if(sampleFrames.some(s=>!fs.existsSync(`${folder}/${s.file}`)))throw new Error('Sample extraction was incomplete');
    const published=spawnSync('python3',[path.resolve(import.meta.dirname,'../homebase_files.py'),id,`${folder}/video.mp4`,row.start_time,'unreviewed'],{encoding:'utf8'});
    if(published.status!==0)throw new Error('Video publish failed: '+published.stderr.slice(0,200));
    const videoFile=JSON.parse(published.stdout).filename;
    const meta={source:'homebase',record:row,channel,fps,videoFrames,wireVideoFrames,audioFrames,authenticatedKeyframes:authenticated,decodeVerified:true,videoFile,sampleFrames,samplePolicy:"three-spaced-frames-v1",downloadedAt:new Date().toISOString()};
    fs.writeFileSync(`${folder}/metadata.json`,JSON.stringify(meta,null,2),{mode:0o600});
    fs.unlinkSync(raw);if(decoded.audio.length)fs.unlinkSync(audioPath);
    if(fs.existsSync(`${folder}/error.json`))fs.unlinkSync(`${folder}/error.json`);
    downloaded++;console.log('Downloaded verified clip',downloaded,'channel',channel,'frames',videoFrames,'audio packets',audioFrames);
   }catch(error){failed++;fs.writeFileSync(`${folder}/error.json`,JSON.stringify({error:error.message,record:row,attempts:(priorError?.attempts??0)+(/GCM key unavailable|session closed/.test(error.message)?0:1),lastAttempt:new Date().toISOString()}),{mode:0o600});console.log('Clip download failed:',error.message);if(/finish within|session closed|GCM key unavailable|investigation limit/.test(error.message))break;}
  }
  console.log('Batch finished:',downloaded,'verified clips;',failed,'failures');
 }else throw new Error('Unknown mode');
}catch(error){console.error(error.message);process.exitCode=1;}finally{await client.disconnect();}
