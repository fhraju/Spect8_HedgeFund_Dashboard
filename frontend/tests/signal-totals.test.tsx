import { afterEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { signalTotals } from "@/lib/signal-totals";
import { hasCurrentEvaluation, resolveSignalCell } from "@/lib/signal-eligibility";
import { SignalSummary } from "@/components/signal-summary";
import type { ConfirmedSignal, CurrentSignals, FormingSignal } from "@/lib/api-types";
const now = Date.parse("2026-09-10T04:40:00Z");
const confirmed = (change: Partial<ConfirmedSignal> = {}): ConfirmedSignal => ({signal_id:"id",instrument_id:"EUR_USD",mode:"MICRO",timeframe:"M30",direction:"BUY",source_bar_start:"2026-09-10T04:00:00Z",source_bar_end:"2026-09-10T04:30:00Z",formed_at:null,confirmed_at:"2026-09-10T04:30:00Z",visible_until:"2026-09-10T05:00:00Z",market_data_source:"MARKET_DATA_PLATFORM",strategy_version:"v1",source_provider:"IG_DEMO",created_at:"2026-09-10T04:30:00Z",...change});
const forming = (change: Partial<FormingSignal> = {}): FormingSignal => ({instrument:"EUR_USD",instrument_id:"EUR_USD",authority:"IG_DEMO",mode:"MICRO",timeframe:"M30",direction:"SELL",state:"FORMING",source_bar_start:"2026-09-10T04:30:00Z",source_bar_end:"2026-09-10T05:00:00Z",formed_at:"2026-09-10T04:40:00Z",source_as_of:"2026-09-10T04:40:00Z",confirmed_at:null,visible_until:null,market_data_source:"MARKET_DATA_PLATFORM",...change});
function data(cs: ConfirmedSignal[] = [], fs: FormingSignal[] = []): CurrentSignals {return {as_of:new Date(now).toISOString(),confirmed:cs,forming:fs,forming_candidates:[['MICRO','M30'],['MICRO','H1'],['MACRO','H1'],['MACRO','H4']].map(([mode,timeframe])=>({authority:"IG_DEMO",instrument_id:"EUR_USD",mode,timeframe,state:"READY",source_as_of:new Date(now).toISOString(),source_bar_end:"2026-09-10T05:00:00Z"}))};}
const count = (d: CurrentSignals | null, mode: "MICRO" | "MACRO" = "MICRO", t = now) => signalTotals(d,mode,["EUR_USD"],t);
afterEach(()=>vi.useRealTimers());
it("Micro counts M30 and H1, excluding Macro and H4",()=>{
 expect(count(data([confirmed(),confirmed({timeframe:"H1"}),confirmed({timeframe:"H4"}),confirmed({mode:"MACRO",timeframe:"H1"})])).confirmed).toBe(2);
});
it("Macro counts both timeframes for one pair and both directions separately",()=>{
 expect(count(data([confirmed({mode:"MACRO",timeframe:"H1"}),confirmed({mode:"MACRO",timeframe:"H4"}),confirmed({mode:"MACRO",timeframe:"H4",direction:"SELL"}),confirmed()]),"MACRO").confirmed).toBe(3);
});
it("deduplicates repeated identities and repaired versions",()=>{
 expect(count(data([confirmed(),confirmed({signal_id:"revision",strategy_version:"v2"})],[forming(),forming()]))).toMatchObject({confirmed:1,forming:1});
});
it("excludes expired, future, invalid and nonmonitored confirmations",()=>{
 expect(count(data([confirmed({visible_until:new Date(now).toISOString()}),confirmed({confirmed_at:"2026-09-10T04:45:00Z"}),confirmed({instrument_id:"GBP_USD"}),confirmed({direction:"NONE"}),confirmed({visible_until:"invalid"})])).confirmed).toBe(0);
});
it("counts forming independently of confirmed precedence in a cell",()=>{
 expect(count(data([confirmed()],[forming(),forming({timeframe:"H1"})]))).toMatchObject({confirmed:1,forming:2});
});
it("counts Macro forming H1/H4 without Micro",()=>{
 expect(count(data([],[forming(),forming({mode:"MACRO",timeframe:"H1"}),forming({mode:"MACRO",timeframe:"H4"})]),"MACRO").forming).toBe(2);
});
it("expires forming and rejects stale/future sources",()=>{
 expect(count(data([],[forming({source_bar_end:new Date(now).toISOString()}),forming({source_as_of:"2026-09-10T04:25:00Z"}),forming({source_as_of:"2026-09-10T04:45:00Z"})])).forming).toBe(0);
});
it("shows unavailable totals on loading, failed or delayed refresh",()=>{
 expect(count(null).confirmed).toBeNull();expect(count({...data(),as_of:"2026-09-10T04:39:00Z"}).confirmed).toBeNull();expect(signalTotals(data(),"MICRO",["EUR_USD"],now,true).forming).toBeNull();
});
it("reports partial coverage and never invents zero for missing inputs",()=>{
 const d=data([],[forming()]);d.forming_candidates![1].state="STALE";expect(count(d)).toMatchObject({forming:1,formingNote:"Available data · 1/2 evaluations ready"});d.forming_candidates![0].state="WAITING_FOR_DATA";expect(count(d).forming).toBeNull();
});
it("ignores wrong-authority forming matches",()=>{expect(count(data([],[forming({authority:"IG_LIVE"})])).forming).toBe(0);});
it("share forming uses M30 freshness and excludes unsupported M30 from readiness",()=>{
 const d=data([], [forming({instrument:"SHARE_MSFT",instrument_id:"SHARE_MSFT",timeframe:"H1",source_as_of:"2026-09-10T04:30:00Z"})]);
 d.forming_candidates = [
  {authority:"IG_DEMO",instrument_id:"SHARE_MSFT",mode:"MICRO",timeframe:"M30",state:"UNSUPPORTED",supported:false,source_as_of:null,source_bar_end:null},
  {authority:"IG_DEMO",instrument_id:"SHARE_MSFT",mode:"MICRO",timeframe:"H1",state:"READY",max_age_seconds:2100,source_as_of:"2026-09-10T04:30:00Z",source_bar_end:"2026-09-10T05:00:00Z"},
 ];
 expect(signalTotals(d,"MICRO",["SHARE_MSFT"],now)).toMatchObject({forming:1,formingNote:"Current provisional signals"});
 d.as_of="2026-09-10T04:55:00Z";
 expect(signalTotals(d,"MICRO",["SHARE_MSFT"],Date.parse(d.as_of)).forming).toBe(1);
 d.as_of="2026-09-10T05:00:00Z";
 expect(signalTotals(d,"MICRO",["SHARE_MSFT"],Date.parse(d.as_of)).forming).toBeNull();
});
it("requires the matching current evaluation for confirmed totals",()=>{
 const d=data([confirmed()]);
 const row=(state:string)=>({instrument_id:"EUR_USD",data_status:"HEALTHY",stale:false,
   current_filter:{status:"NONE",filter_freshness:"CURRENT"},
   evaluation_candidates:[{mode:"MICRO",timeframe:"M30",state}]} as never);
 expect(signalTotals(d,"MICRO",[row("STALE")],now).confirmed).toBe(0);
 expect(signalTotals(d,"MICRO",[row("BLOCKED")],now).confirmed).toBe(0);
 expect(signalTotals(d,"MICRO",[row("CURRENT")],now).confirmed).toBe(1);
});
const scannerRow=(candidates:{mode:string;timeframe:string;state:string}[])=>({instrument_id:"EUR_USD",data_status:"HEALTHY",stale:false,current_filter:{status:"NONE",filter_freshness:"CURRENT"},evaluation_candidates:candidates} as never);
const cardIsLive=(d:CurrentSignals,row:unknown,timeframe="M30")=>resolveSignalCell({data:d,failed:false,now,instrumentId:"EUR_USD",mode:"MICRO",timeframe,evaluationCurrent:hasCurrentEvaluation(row as never,"MICRO",timeframe)}).kind==="live";
it("counts forming only when its matching evaluation is CURRENT",()=>{
 // A. matching evaluation CURRENT -> counted
 expect(signalTotals(data([],[forming()]),"MICRO",[scannerRow([{mode:"MICRO",timeframe:"M30",state:"CURRENT"}])],now).forming).toBe(1);
 // B. matching evaluation STALE -> not current
 expect(signalTotals(data([],[forming()]),"MICRO",[scannerRow([{mode:"MICRO",timeframe:"M30",state:"STALE"}])],now).forming).toBe(0);
 // C. matching evaluation BLOCKED -> not current
 expect(signalTotals(data([],[forming()]),"MICRO",[scannerRow([{mode:"MICRO",timeframe:"M30",state:"BLOCKED"}])],now).forming).toBe(0);
});
it("keeps forming eligible when an unrelated timeframe is blocked",()=>{
 // D. only the matching timeframe matters
 const row=scannerRow([{mode:"MICRO",timeframe:"M30",state:"CURRENT"},{mode:"MICRO",timeframe:"H1",state:"BLOCKED"}]);
 expect(signalTotals(data([],[forming()]),"MICRO",[row],now).forming).toBe(1);
});
it("excludes expired or superseded forming evidence with rows present",()=>{
 // E. expired bar and stale source evidence are never counted
 const row=scannerRow([{mode:"MICRO",timeframe:"M30",state:"CURRENT"}]);
 expect(signalTotals(data([],[forming({source_bar_end:new Date(now).toISOString()})]),"MICRO",[row],now).forming).toBe(0);
 expect(signalTotals(data([],[forming({source_as_of:"2026-09-10T04:25:00Z"})]),"MICRO",[row],now).forming).toBe(0);
});
it("forming cards and totals agree on matching evaluation eligibility",()=>{
 const stale=scannerRow([{mode:"MICRO",timeframe:"M30",state:"STALE"}]);
 const d=data([],[forming()]);
 expect(cardIsLive(d,stale)).toBe(false);
 expect(signalTotals(d,"MICRO",[stale],now).forming).toBe(0);
 const current=scannerRow([{mode:"MICRO",timeframe:"M30",state:"CURRENT"}]);
 expect(cardIsLive(d,current)).toBe(true);
 expect(signalTotals(d,"MICRO",[current],now).forming).toBe(1);
});
it("cards and totals agree on confirmation eligibility",()=>{
 const cases:Array<[ConfirmedSignal,string,boolean]>=[
  [confirmed(),"CURRENT",true],
  [confirmed({confirmed_at:"invalid"}),"CURRENT",false],
  [confirmed({confirmed_at:"2026-09-10T04:45:00Z"}),"CURRENT",false],
  [confirmed({visible_until:new Date(now).toISOString()}),"CURRENT",false],
  [confirmed({source_bar_start:"invalid"}),"CURRENT",false],
  [confirmed({direction:"NONE"}),"CURRENT",false],
  [confirmed(),"STALE",false],
  [confirmed(),"BLOCKED",false],
 ];
 for(const [signal,state,eligible] of cases){
  const row=scannerRow([{mode:"MICRO",timeframe:"M30",state}]);
  const d=data([signal]);
  expect({eligible,total:signalTotals(d,"MICRO",[row],now).confirmed,live:cardIsLive(d,row)}).toEqual({eligible,total:eligible?1:0,live:eligible});
 }
});
it("cards and totals agree when an unrelated timeframe is blocked",()=>{
 const row=scannerRow([{mode:"MICRO",timeframe:"M30",state:"CURRENT"},{mode:"MICRO",timeframe:"H1",state:"BLOCKED"}]);
 const d=data([confirmed()]);
 expect(signalTotals(d,"MICRO",[row],now).confirmed).toBe(1);
 expect(cardIsLive(d,row)).toBe(true);
});
it("renders existing card classes and separate mode totals",()=>{
 vi.useFakeTimers();vi.setSystemTime(now);const d=data([confirmed(),confirmed({mode:"MACRO",timeframe:"H1"}),confirmed({mode:"MACRO",timeframe:"H4"})],[forming()]);
 const micro=renderToStaticMarkup(<SignalSummary data={d} mode="MICRO" instrumentIds={['EUR_USD']} now={now}/>);
 const macro=renderToStaticMarkup(<SignalSummary data={d} mode="MACRO" instrumentIds={['EUR_USD']} now={now}/>);
 expect(micro).toContain('<b>1</b><small>Confirmed Signals</small>');expect(micro).toContain('<b>1</b><small>Forming Signals</small>');expect(macro).toContain('<b>2</b><small>Confirmed Signals</small>');expect(macro).toContain('Macro · H1 + H4');expect(micro).toContain('class="kpi"');
});
