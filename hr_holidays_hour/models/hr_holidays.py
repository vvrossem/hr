# -*- coding: utf-8 -*-
# Copyright 2017 Onestein (<http://www.onestein.eu>)
# Copyright 2019 Coop IT Easy SCRLfs
#   - Vincent Van Rossem <vincent@coopiteasy.be>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import datetime
import logging

from openerp import _, api, fields, models
from openerp.osv import osv
from openerp.exceptions import ValidationError
from openerp.exceptions import Warning as UserError

_logger = logging.getLogger(__name__)


class HrHolidays(osv.osv):
    _inherit = "hr.holidays"

    # workaround to override constraints

    def _check_date(self, cr, uid, ids, context=None):
        return True

    _check_holidays = lambda self, cr, uid, ids, context=None: self.check_holidays(
        cr, uid, ids, context=context
    )

    _constraints = [
        (
            _check_date,
            "You can not have 2 leaves that overlaps on same day!",
            ["date_from", "date_to"],
        ),
        (
            _check_holidays,
            "The number of remaining leaves is not sufficient for this leave type.\n"
            "Please verify also the leaves waiting for validation.",
            ["state", "number_of_days_temp"],
        ),
    ]

    def check_holidays(self, cr, uid, ids, context=None):
        return True


class HrHolidays(models.Model):
    _inherit = "hr.holidays"

    number_of_hours_temp = fields.Float(
        string="Allocation in Hours",
        digits=(2, 2),
        readonly=True,
        states={
            "draft": [("readonly", False)],
            "confirm": [("readonly", False)],
        },
    )
    number_of_hours = fields.Float(
        compute="_compute_number_of_hours", store=True
    )
    virtual_hours = fields.Float(
        compute="_compute_number_of_hours", store=True
    )
    working_hours = fields.Float(digits=(2, 2))

    @api.onchange("employee_id")
    def onchange_employee(self):
        # Get result of original onchange_employee from parent class:
        res = super(HrHolidays, self).onchange_employee(self.employee_id.id)

        # Workaround for api incompatibility:
        if type(res) is dict and res.has_key("value"):
            for field, value in res.get("value").items():
                if hasattr(self, field):
                    setattr(self, field, value)

        # Additional code
        self.department_id = None
        self.number_of_hours_temp = 0.0
        if self.employee_id:
            self._set_number_of_hours_temp()
            self.department_id = self.employee_id.department_id

    @api.onchange("date_from", "date_to")
    def onchange_date(self):
        # Check in context what form is open: add or remove
        if self.env.context.get("default_type", "") == "add":
            return

        self._check_dates()
        self._check_employee()
        self._set_number_of_hours_temp()

    @api.onchange("date_from")
    def onchange_date_from(self):
        # Get result of original onchange_employee from parent class:
        res = super(HrHolidays, self).onchange_date_from(
            self.date_to, self.date_from
        )

        # Workaround for api incompatibility:
        if type(res) is dict and res.has_key("value"):
            for field, value in res.get("value").items():
                if hasattr(self, field):
                    setattr(self, field, value)

    @api.onchange("date_to")
    def onchange_date_to(self):
        # Get result of original onchange_employee from parent class:
        res = super(HrHolidays, self).onchange_date_to(
            self.date_to, self.date_from
        )

        # Workaround for api incompatibility:
        if type(res) is dict and res.has_key("value"):
            for field, value in res.get("value").items():
                if hasattr(self, field):
                    setattr(self, field, value)

    @api.multi
    def _set_number_of_hours_temp(self):
        self.ensure_one()
        from_dt = self._compute_datetime(self.date_from)
        to_dt = self._compute_datetime(self.date_to)
        work_hours = self._compute_work_hours(from_dt, to_dt)
        self.number_of_hours_temp = work_hours

    @api.model
    def _compute_datetime(self, date):
        dt = False
        if date:
            this_year = datetime.date.today().year
            reference_date = fields.Datetime.context_timestamp(
                self.env.user, datetime.datetime(this_year, 1, 1, 12)
            )
            dt = fields.Datetime.from_string(date)
            tz_dt = fields.Datetime.context_timestamp(self.env.user, dt)
            dt = dt + tz_dt.tzinfo._utcoffset
            dt = dt - reference_date.tzinfo._utcoffset
        return dt

    @api.multi
    def _check_dates(self):
        self.ensure_one()
        # date_to has to be greater than date_from
        if self.date_from and self.date_to:
            if self.date_from > self.date_to:
                raise UserError(
                    _("The start date must be anterior to the end date.")
                )

    @api.multi
    def _check_employee(self):
        self.ensure_one()
        employee = self.employee_id
        if not employee and (self.date_to or self.date_from):
            raise UserError(_("Set an employee first!"))

    @api.multi
    def _compute_work_hours(self, from_dt, to_dt):
        self.ensure_one()
        employee = self.employee_id
        work_hours = 0.0
        if self.date_from and self.date_to:
            working_hours = self._get_working_hours(employee)
            for working_hour in working_hours:
                wh = working_hour.get_working_hours(
                    from_dt,
                    to_dt,
                    compute_leaves=True,
                    resource_id=employee.resource_id.id,
                )
                if wh:
                    work_hours += wh[0]
        return work_hours

    @api.model
    def _get_working_hours(self, employee):
        working_hours = []
        if employee.calendar_id:
            working_hours.append(employee.calendar_id)
        else:
            contracts = employee.sudo().contract_ids
            for contract in contracts:
                if contract.working_hours:
                    working_hours.append(contract.working_hours)
        return working_hours

    @api.depends("number_of_hours_temp", "state")
    def _compute_number_of_hours(self):
        for rec in self:
            number_of_hours = rec.number_of_hours_temp
            if rec.type == "remove":
                number_of_hours = -rec.number_of_hours_temp

            rec.virtual_hours = number_of_hours
            if rec.state not in ("validate",):
                number_of_hours = 0.0
            rec.number_of_hours = number_of_hours

    @api.constrains("holiday_type", "type", "employee_id", "holiday_status_id")
    def _check_holidays(self):
        for holiday in self:
            if holiday.holiday_type != "employee" or holiday.type != "remove":
                continue
            if holiday.employee_id and not holiday.holiday_status_id.limit:
                leave_hours = holiday.holiday_status_id.get_hours(
                    holiday.employee_id
                )
                holiday._check_leave_hours(leave_hours)

    @api.model
    def _check_leave_hours(self, leave_hours):
        remaining = leave_hours["remaining_hours"]
        virt_remaining = leave_hours["virtual_remaining_hours"]
        if remaining < 0 or virt_remaining < 0:
            # Raising a warning gives a more user-friendly
            # feedback than the default constraint error
            raise ValidationError(
                _(
                    "The number of remaining hours is not sufficient for "
                    "this leave type.\nPlease check for allocation requests "
                    "awaiting validation."
                )
            )

    @api.multi
    def name_get(self):
        res = []
        for leave in self:
            res.append(
                (
                    leave.id,
                    _("%s on %s : %.2f hour(s)")
                    % (
                        leave.employee_id.name,
                        leave.holiday_status_id.name,
                        leave.number_of_hours_temp,
                    ),
                )
            )
        return res

    @api.multi
    def holidays_validate(self):
        # TODO compare with 10.0 and confirm it's correctly inherited
        res = super(HrHolidays, self).holidays_validate()
        for holiday in self:
            if holiday.holiday_type == "category":
                for employee in holiday.category_id.employee_ids:
                    self.write(
                        {"number_of_hours_temp": holiday.number_of_hours_temp}
                    )
        return res
