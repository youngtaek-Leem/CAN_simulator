import type { ComponentType } from 'react';
import type { WidgetConfig, WidgetType } from '../types';
import { CanMessageDisplay, RxSignalDisplay, TextDisplay } from './displays';
import { ButtonWidget, CheckboxWidget, DropdownWidget, ManualValueWidget, SliderWidget } from './controls';
import {
  MultiButtonWidget,
  MultiCheckboxWidget,
  MultiDropdownWidget,
  MultiManualValueWidget,
  MultiSliderWidget,
  FunctionMultiButtonWidget,
  RandomMultiButtonWidget,
} from './MultiControls';
import { TxBox } from './TxBox';
import { ReplayBox } from './ReplayBox';
import { IsoTpBox } from './IsoTpBox';
import { GraphWidget } from './GraphWidget';
import { TestRunnerBox } from './TestRunnerBox';
import { FunctionButtonWidget } from './FunctionButtonWidget';
import { RandomButtonWidget } from './RandomButtonWidget';
import UdsSwdlWidget from './UdsSwdlWidget';
import OtaTesterWidget from './OtaTesterWidget';
import { AudioMonitorWidget } from './AudioMonitorWidget';
import { PowerControlWidget } from './PowerControlWidget';
import { CanAudioLatencyWidget } from './CanAudioLatencyWidget';

interface WidgetMeta {
  label: string;
  component: ComponentType<{ config: WidgetConfig }>;
  defaultSize: { w: number; h: number; minW: number; minH: number };
}

export const WIDGET_REGISTRY: Record<WidgetType, WidgetMeta> = {
  button: {
    label: '버튼',
    component: ButtonWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  checkbox: {
    label: '체크박스',
    component: CheckboxWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  dropdown: {
    label: '드롭다운',
    component: DropdownWidget,
    defaultSize: { w: 2, h: 2, minW: 2, minH: 1 },
  },
  slider: {
    label: '슬라이더',
    component: SliderWidget,
    defaultSize: { w: 4, h: 3, minW: 2, minH: 1 },
  },
  manualValue: {
    label: '입력 박스',
    component: ManualValueWidget,
    defaultSize: { w: 2, h: 3, minW: 2, minH: 1 },
  },
  functionButton: {
    label: 'Function 버튼',
    component: FunctionButtonWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  randomButton: {
    label: 'Random 버튼',
    component: RandomButtonWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  multiButton: {
    label: '멀티 버튼',
    component: MultiButtonWidget,
    defaultSize: { w: 6, h: 5, minW: 1, minH: 2 },
  },
  multiCheckbox: {
    label: '멀티 체크박스',
    component: MultiCheckboxWidget,
    defaultSize: { w: 6, h: 5, minW: 1, minH: 2 },
  },
  multiDropdown: {
    label: '멀티 드롭다운',
    component: MultiDropdownWidget,
    defaultSize: { w: 6, h: 5, minW: 1, minH: 2 },
  },
  multiSlider: {
    label: '멀티 슬라이더',
    component: MultiSliderWidget,
    defaultSize: { w: 8, h: 5, minW: 1, minH: 2 },
  },
  multiManualValue: {
    label: '멀티 입력 박스',
    component: MultiManualValueWidget,
    defaultSize: { w: 6, h: 5, minW: 1, minH: 2 },
  },
  functionMultiButton: {
    label: '멀티 Function 버튼',
    component: FunctionMultiButtonWidget,
    defaultSize: { w: 6, h: 5, minW: 1, minH: 2 },
  },
  randomMultiButton: {
    label: '멀티 Random 버튼',
    component: RandomMultiButtonWidget,
    defaultSize: { w: 6, h: 5, minW: 1, minH: 2 },
  },
  txBox: {
    label: 'CAN 메시지 전송[IG]',
    component: TxBox,
    defaultSize: { w: 7, h: 10, minW: 4, minH: 3 },
  },
  isotpTx: {
    label: 'ISO-TP 메시지 전송[UDS]',
    component: IsoTpBox,
    defaultSize: { w: 7, h: 7, minW: 1, minH: 2 },
  },
  replayBox: {
    label: 'CAN 로그 Replay',
    component: ReplayBox,
    defaultSize: { w: 8, h: 7, minW: 3, minH: 2 },
  },
  testRunner: {
    label: '테스트 Sequence 실행기',
    component: TestRunnerBox,
    defaultSize: { w: 11, h: 12, minW: 4, minH: 3 },
  },
  signalGraph: {
    label: 'CAN 신호 그래프',
    component: GraphWidget,
    defaultSize: { w: 9, h: 6, minW: 1, minH: 2 },
  },
  canMessageDisplay: {
    label: 'CAN 메시지 표시창',
    component: CanMessageDisplay,
    defaultSize: { w: 8, h: 11, minW: 3, minH: 2 },
  },
  rxSignalDisplay: {
    label: '수신 CAN 신호 표시창',
    component: RxSignalDisplay,
    defaultSize: { w: 8, h: 11, minW: 3, minH: 2 },
  },
  textDisplay: {
    label: '텍스트 표시창',
    component: TextDisplay,
    defaultSize: { w: 8, h: 11, minW: 2, minH: 2 },
  },
  udsSwdl: {
    label: 'CAN-SWDL',
    component: UdsSwdlWidget,
    defaultSize: { w: 12, h: 20, minW: 4, minH: 3 },
  },
  otaTester: {
    label: 'OTA Tester',
    component: OtaTesterWidget,
    defaultSize: { w: 13, h: 20, minW: 4, minH: 3 },
  },
  audioMonitor: {
    label: '오디오 신호 모니터',
    component: AudioMonitorWidget,
    defaultSize: { w: 11, h: 12, minW: 3, minH: 2 },
  },
  powerControl: {
    label: '전원 컨트롤',
    component: PowerControlWidget,
    defaultSize: { w: 6, h: 14, minW: 3, minH: 6 },
  },
  canAudioLatency: {
    label: 'CAN-오디오 지연 확인',
    component: CanAudioLatencyWidget,
    defaultSize: { w: 8, h: 10, minW: 3, minH: 3 },
  },
};
