###
### Copyright (C) 2022 Intel Corporation
###
### SPDX-License-Identifier: BSD-3-Clause
###

import slash

from ...lib.common import timefn, get_media, call, exe2os, filepath2os
from ...lib.ffmpeg.util import have_ffmpeg, BaseFormatMapper
from ...lib.mixin.vpp import VppMetricMixin

from ...lib import metrics2

@slash.requires(have_ffmpeg)
class BaseVppTest(slash.Test, BaseFormatMapper, VppMetricMixin):
  def before(self):
    self.refctx = []
    self.renderDevice = get_media().render_device
    self.post_validate = lambda: None

  def get_input_formats(self):
    return self.caps.get("ifmts", [])

  def get_output_formats(self):
    return self.caps.get("ofmts", [])

  def gen_vpp_opts(self):
    raise NotImplementedError

  def gen_input_opts(self):
    if self.vpp_op not in ["deinterlace"]:
      opts = "-f rawvideo -pix_fmt {mformat} -s:v {width}x{height}"
    else:
      opts = "-c:v {ffdecoder}"
    opts += " -i {ossource}"

    return opts

  def gen_output_opts(self):
    vpfilter = self.gen_vpp_opts()
    vpfilter.append("hwdownload")
    vpfilter.append("format={ohwformat}")

    opts = "-filter_complex" if self.vpp_op in ["composite"] else "-vf"
    opts += f" '{','.join(vpfilter)}'"
    opts += " -pix_fmt {mformat}" if self.vpp_op not in ["csc"] else ""
    opts += " -f rawvideo -fps_mode passthrough -an -vframes {frames} -y {osdecoded}"

    return opts

  def gen_name(self):
    name = "{case}_{vpp_op}"
    name += dict(
      brightness  = "_{level}_{width}x{height}_{format}",
      contrast    = "_{level}_{width}x{height}_{format}",
      hue         = "_{level}_{width}x{height}_{format}",
      saturation  = "_{level}_{width}x{height}_{format}",
      denoise     = "_{level}_{width}x{height}_{format}",
      scale       = "_{scale_width}x{scale_height}_{format}",
      scale_qsv   = "_{scale_width}x{scale_height}_{format}",
      sharpen     = "_{level}_{width}x{height}_{format}",
      deinterlace = "_{method}_{rate}_{width}x{height}_{format}",
      csc         = "_{width}x{height}_{format}_to_{csc}",
      transpose   = "_{degrees}_{method}_{width}x{height}_{format}",
      composite   = "_{owidth}x{oheight}_{format}",
    )[self.vpp_op]

    if vars(self).get("r2r", None) is not None:
      name += "_r2r"

    return name

  @timefn("ffmpeg:vpp")
  def call_ffmpeg(self, iopts, oopts):
    call(
      f"{exe2os('ffmpeg')} -hwaccel {self.hwaccel}"
      f" -init_hw_device {self.hwaccel}=hw:{self.hwdevice}"
      f" -hwaccel_output_format {self.hwaccel}"
      f" -v verbose {iopts} {oopts}"
    )

  def validate_caps(self):
    ifmts         = self.get_input_formats()
    ofmts         = self.get_output_formats()
    self.ifmt     = self.format
    self.ofmt     = self.format if "csc" != self.vpp_op else self.csc
    self.mformat  = self.map_format(self.format)

    if self.mformat is None:
      slash.skip_test(f"ffmpeg.{self.format} unsupported")

    if self.vpp_op in ["csc"]:
      self.ihwformat = self.map_format(self.ifmt if self.ifmt in ifmts else None)
      self.ohwformat = self.map_format(self.ofmt if self.ofmt in ofmts else None)
    else:
      self.ihwformat = self.map_best_hw_format(self.ifmt, ifmts)
      self.ohwformat = self.map_best_hw_format(self.ofmt, ofmts)

    if self.ihwformat is None:
      slash.skip_test(f"{self.ifmt} unsupported")
    if self.ohwformat is None:
      slash.skip_test(f"{self.ofmt} unsupported")

    if self.vpp_op in ["composite"]:
      self.owidth, self.oheight = self.width, self.height
      for comp in self.comps:
        self.owidth = max(self.owidth, self.width + comp['x'])
        self.oheight = max(self.oheight, self.height + comp['y'])

    self.post_validate()

  def vpp(self):
    self.validate_caps()

    iopts = self.gen_input_opts()
    oopts = self.gen_output_opts()
    name  = self.gen_name().format(**vars(self))

    self.decoded    = get_media()._test_artifact(f"{name}.yuv")
    self.ossource   = filepath2os(self.source)
    self.osdecoded  = filepath2os(self.decoded)
    self.call_ffmpeg(iopts.format(**vars(self)), oopts.format(**vars(self)))

    if vars(self).get("r2r", None) is not None:
      assert type(self.r2r) is int and self.r2r > 1, "invalid r2r value"

      metric = metrics2.factory.create(metric = dict(type = "md5", numbytes = -1))
      metric.update(filetest = self.decoded)
      metric.expect = metric.actual # the first run is our reference for r2r
      metric.check()

      get_media()._purge_test_artifact(self.decoded)

      for i in range(1, self.r2r):
        self.decoded = get_media()._test_artifact(f"{name}_{i}.yuv")
        self.osdecoded = filepath2os(self.decoded)
        self.call_ffmpeg(iopts.format(**vars(self)), oopts.format(**vars(self)))

        metric.update(filetest = self.decoded)
        metric.check()

        #delete output file after each iteration
        get_media()._purge_test_artifact(self.decoded)
    else:
      self.check_metrics()