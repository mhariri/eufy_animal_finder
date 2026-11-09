/** Decode authenticated archive chunks, grouping contiguous parts by picture sequence and timestamp. */
import crypto from 'node:crypto';
import { VideoFrameDecoder, AccessUnitAssembler, parseVideoFrameHeader } from '@mega-yfue/eufy-sdk';
export function decodeHomebaseFrames(frames, privateKey, expectedPictures=0) {
  const decoder=new VideoFrameDecoder(privateKey);
  const video=[],audio=[];
  let authenticated=0,failedFrames=0,fps=15,videoFrames=0,wireVideoFrames=0;
  const assembler=new AccessUnitAssembler(()=>{failedFrames++;});
  for(const f of frames){
    if(f.commandId===1300){
      const h=parseVideoFrameHeader(f.raw);if(!h){failedFrames++;continue;}
      wireVideoFrames++;
      const rate=f.raw.readUInt16LE(8);if(rate>0&&rate<=120)fps=rate;
      const units=assembler.push(f.raw,raw=>{
        if(h.keyframe){const d=decoder.decodeFrame(raw);if(d){authenticated++;return d.h264;}failedFrames++;return;}
        if(raw.length>=22+h.payloadLength)return raw.subarray(22,22+h.payloadLength);
        failedFrames++;
      });
      for(const unit of units){video.push(unit.data);videoFrames++;}
    }else if(f.commandId===1301&&f.raw.length>=44&&f.raw[5]===0&&decoder.currentMediaKey){
      const length=f.raw.readUInt32LE(0);if(f.raw.length<44+length)continue;
      try {
        const d=crypto.createDecipheriv('aes-256-gcm',decoder.currentMediaKey,f.raw.subarray(32,44));
        d.setAAD(Buffer.from('eufy security'));d.setAuthTag(f.raw.subarray(16,32));
        audio.push(Buffer.concat([d.update(f.raw.subarray(44,44+length)),d.final()]));
      }catch{}
    }
  }
  assembler.discard();
  if(!authenticated||failedFrames||!videoFrames)throw new Error('Video authentication or assembly failed');
  if(expectedPictures>0&&videoFrames!==expectedPictures)throw new Error(`Frame count mismatch: ${videoFrames} pictures vs ${expectedPictures}`);
  return {video:Buffer.concat(video),audio:Buffer.concat(audio),fps,videoFrames,wireVideoFrames,authenticated,audioFrames:audio.length};
}
