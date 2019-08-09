# Copyright 2015 Ocado Innovation Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Models for closuretree application."""

# We like magic.
# pylint: disable=W0142

# We have lots of dynamically generated things, hard for pylint to solve.
# pylint: disable=E1101

# It may not be our class, but we made the attribute on it
# pylint: disable=W0212

# Public methods are useful!
# pylint: disable=R0904

from django.core.exceptions import ObjectDoesNotExist
from django.db import models, connection
from django.db.models import F
from django.db.models.base import ModelBase
from django.db.models.signals import pre_save, post_save, pre_delete
from django.dispatch import receiver
from django.utils.six import with_metaclass
from . import managers
import sys
import uuid


def _closure_model_unicode(self):
    """__unicode__ implementation for the dynamically created
        <Model>Closure model.
    """
    return "Closure from %s to %s" % (self.parent, self.child)

def create_closure_model(cls):
    """Creates a <Model>Closure model in the same module as the model."""
    meta_vals = {
        'unique_together':  (("parent", "child"),),
        'app_label': cls._meta.app_label,
    }
    if getattr(cls._meta, 'db_table', None):
        meta_vals['db_table'] = '%sclosure' % getattr(cls._meta, 'db_table')

    closure_cls_name = '%sClosure' % cls.__name__
    parent_field = models.ForeignKey(
        cls.__name__,
        related_name=cls.closure_parentref()
    )
    model = type(closure_cls_name, (models.Model,), {
        'parent': parent_field,
        'child': models.ForeignKey(
            cls.__name__,
            related_name=cls.closure_childref()
        ),
        'depth': models.IntegerField(),
        '__module__':   cls.__module__,
        '__unicode__': _closure_model_unicode,
        'Meta': type('Meta', (object,), meta_vals),
    })
    setattr(cls, "_closure_model", model)
    return model

class ClosureModelBase(ModelBase):
    """Metaclass for Models inheriting from ClosureModel,
        to ensure the <Model>Closure model is created.
    """
    #This is a metaclass. MAGIC!
    def __init__(cls, name, bases, dct):
        """Create the closure model in addition
            to doing all the normal django stuff.
        """
        super(ClosureModelBase, cls).__init__(name, bases, dct)
        if not cls._meta.get_parent_list() and not cls._meta.abstract:
            setattr(
                sys.modules[cls.__module__],
                '%sClosure' % cls.__name__,
                create_closure_model(cls)
            )

class ClosureModel(with_metaclass(ClosureModelBase, models.Model)):
    """Provides methods to assist in a tree based structure."""
    # pylint: disable=W5101

    level = models.PositiveIntegerField(editable=False, db_index=True, default=0)
    objects = managers.CttManager()

    class Meta:
        """We make this an abstract class, it needs to be inherited from."""
        # pylint: disable=W0232
        # pylint: disable=R0903
        abstract = True

    def __setattr__(self, name, value):
        if name.endswith('_id'):
            id_field_name = name
        else:
            id_field_name = "%s_id" % name
        if (
            name.startswith(self._closure_sentinel_attr) and  # It's the right attribute
            (  # It's already been set
                (hasattr(self, 'get_deferred_fields') and id_field_name not in self.get_deferred_fields() and hasattr(self, id_field_name)) or  # Django>=1.8
                (not hasattr(self, 'get_deferred_fields') and hasattr(self, id_field_name))  # Django<1.8
            ) and
            not self._closure_change_check()  # The old value isn't stored
        )   :
            if name.endswith('_id'):
                obj_id = value
            elif value:
                obj_id = value.pk
            else:
                obj_id = None
            # If this is just setting the same value again, we don't need to do anything
            if getattr(self, id_field_name) != obj_id:
                # Already set once, and not already stored the old
                # value, need to take a copy before it changes
                self._closure_change_init()
        super(ClosureModel, self).__setattr__(name, value)

    @classmethod
    def _toplevel(cls):
        """Find the top level of the chain we're in.

            For example, if we have:
            C inheriting from B inheriting from A inheriting from ClosureModel
            C._toplevel() will return A.
        """
        superclasses = (
            list(set(ClosureModel.__subclasses__()) &
                 set(cls._meta.get_parent_list()))
        )
        return next(iter(superclasses)) if superclasses else cls

    @classmethod
    def rebuildtable(cls):
        """Regenerate the entire closuretree."""
        cls._closure_model.objects.all().delete()
        for node in cls.objects.order_by('level'):
            node._closure_createlink()

    @classmethod
    def closure_parentref(cls):
        """How to refer to parents in the closure tree"""
        return "%sclosure_children" % cls._toplevel().__name__.lower()

    # Backwards compatibility:
    _closure_parentref = closure_parentref

    @classmethod
    def closure_childref(cls):
        """How to refer to children in the closure tree"""
        return "%sclosure_parents" % cls._toplevel().__name__.lower()

    # Backwards compatibility:
    _closure_childref = closure_childref

    @property
    def _closure_sentinel_attr(self):
        """The attribute we need to watch to tell if the
            parent/child relationships have changed
        """
        meta = getattr(self, 'ClosureMeta', None)
        return getattr(meta, 'sentinel_attr', self._closure_parent_attr)

    @property
    def _closure_parent_attr(self):
        '''The attribute or property that holds the parent object.'''
        meta = getattr(self, 'ClosureMeta', None)
        return getattr(meta, 'parent_attr', 'parent')

    @property
    def _closure_parent_pk(self):
        """What our parent pk is in the closure tree."""
        if hasattr(self, "%s_id" % self._closure_parent_attr):
            return getattr(self, "%s_id" % self._closure_parent_attr)
        else:
            parent = getattr(self, self._closure_parent_attr)
            return parent.pk if parent else None

    def _closure_deletelink(self):
        """Remove incorrect links from the closure tree."""
        qs = self._closure_model.objects.filter(child_id=self.pk)
        qs.delete()

    @property
    def _closure_parent(self):
        result = None
        try:
            result = getattr(self, self._closure_parent_attr)
        except ObjectDoesNotExist:
            pass
        return result

    def _closure_createlink(self):
        pk = self.pk
        parent_id = self._closure_parent_pk
        closure_table = self._closure_model._meta.db_table
        selects = ["SELECT 0, %s, %s"]
        query_args = [pk, pk]
        if parent_id:
            template = ("SELECT depth + 1, %s, parent_id "
                        "FROM {table} WHERE child_id = %s")
            selects.append(template.format(table=closure_table))
            query_args.extend([pk, parent_id])
        if isinstance(pk, uuid.UUID):
            query_args = map(unicode, query_args)

        insert_template = (
            "{insert_operator} INTO {closure_table} (depth, child_id, parent_id) {selects_union} {on_conflict}"
        )
        on_conflict_clause = ''

        if connection.vendor == 'sqlite':
            insert_operator = 'INSERT OR IGNORE'
        elif connection.vendor == 'mysql':
            insert_operator = 'INSERT IGNORE'
        elif connection.vendor == 'postgresql':
            insert_operator = 'INSERT'
            on_conflict_clause = 'ON CONFLICT DO NOTHING'
        else:
            insert_operator = 'INSERT'

        query_sql = insert_template.format(
            insert_operator=insert_operator,
            on_conflict=on_conflict_clause,
            closure_table=closure_table,
            selects_union=' UNION '.join(selects),
        )
        with connection.cursor() as cursor:
            cursor.execute(query_sql, query_args)

    def get_ancestors(self, include_self=False, depth=None):
        """Return all the ancestors of this object."""
        if self.is_root_node():
            if not include_self:
                return self._toplevel().objects.none()
            else:
                # Filter on pk for efficiency.
                return self._toplevel().objects.filter(pk=self.pk)

        params = {"%s__child" % self._closure_parentref():self.pk}
        if depth is not None:
            params["%s__depth__lte" % self._closure_parentref()] = depth
        ancestors = self._toplevel().objects.filter(**params)
        if not include_self:
            ancestors = ancestors.exclude(pk=self.pk)
        return ancestors.order_by("level")

    def get_descendants(self, include_self=False, depth=None):
        """Return all the descendants of this object."""
        params = {"%s__parent" % self._closure_childref():self.pk}
        if depth is not None:
            params["%s__depth__lte" % self._closure_childref()] = depth
        descendants = self._toplevel().objects.filter(**params)
        if not include_self:
            descendants = descendants.exclude(pk=self.pk)
        return descendants.order_by("%s__depth" % self._closure_childref())

    def prepopulate(self, queryset):
        """Perpopulate a descendants query's children efficiently.
            Call like: blah.prepopulate(blah.get_descendants().select_related(stuff))
        """
        objs = list(queryset)
        hashobjs = dict([(x.pk, x) for x in objs] + [(self.pk, self)])
        for descendant in hashobjs.values():
            descendant._cached_children = []
        for descendant in objs:
            assert descendant._closure_parent_pk in hashobjs
            parent = hashobjs[descendant._closure_parent_pk]
            parent._cached_children.append(descendant)

    def get_children(self):
        """Return all the children of this object."""
        if hasattr(self, '_cached_children'):
            children = self._toplevel().objects.filter(
                pk__in=[n.pk for n in self._cached_children]
            )
            children._result_cache = self._cached_children
            return children
        else:
            return self.get_descendants(include_self=False, depth=1)

    def get_root(self):
        """Return the furthest ancestor of this node."""
        if self.is_root_node():
            return self

        return self.get_ancestors().order_by(
            "-%s__depth" % self._closure_parentref()
        )[0]

    def is_child_node(self):
        """Is this node a child, i.e. has a parent?"""
        return not self.is_root_node()

    def is_root_node(self):
        """Is this node a root, i.e. has no parent?"""
        return self._closure_parent_pk is None

    def is_descendant_of(self, other, include_self=False):
        """Is this node a descendant of `other`?"""
        if other.pk == self.pk:
            return include_self

        return self._closure_model.objects.filter(
            parent=other,
            child=self
        ).exclude(pk=self.pk).exists()

    def is_ancestor_of(self, other, include_self=False):
        """Is this node an ancestor of `other`?"""
        return other.is_descendant_of(self, include_self=include_self)

    def _closure_change_init(self):
        """Part of the change detection. Setting up"""
        # More magic. We're setting this inside setattr...
        # pylint: disable=W0201
        self._closure_old_parent = self._closure_parent

    def _closure_change_check(self):
        """Part of the change detection. Have we changed since we began?"""
        return hasattr(self,"_closure_old_parent")

    def _closure_update_links(self):
        new_parent = self._closure_parent
        old_parent = self._closure_old_parent
        subtree_with_self = self.get_descendants(include_self=True)
        subtree_without_self = self.get_descendants().order_by('level')
        cached_subtree = [self] + list(subtree_without_self)
        new_level = new_parent.level if new_parent is not None else 0
        old_level = old_parent.level if old_parent is not None else 0
        leveldiff = new_level - old_level
        subtree_without_self.update(level=F('level') + leveldiff)
        links = self._closure_model.objects.filter(child_id__in=subtree_with_self)
        links.delete()
        for item in cached_subtree:
            item._closure_createlink()


@receiver(pre_save, dispatch_uid='closure-model-presave')
def closure_model_set_level(sender, **kwargs):
    if issubclass(sender, ClosureModel):
        instance = kwargs['instance']
        if instance._closure_parent_pk:
            parent = getattr(instance, instance._closure_parent_attr)
            instance.level = parent.level + 1
        else:
            instance.level = 0


@receiver(post_save, dispatch_uid='closure-model-save')
def closure_model_save(sender, **kwargs):
    if issubclass(sender, ClosureModel):
        instance = kwargs['instance']
        create = kwargs['created']
        if instance._closure_change_check():
            # Changed parents
            instance._closure_update_links()
            delattr(instance, "_closure_old_parent")
        elif create:
            # We still need to create links when we're first made
            instance._closure_createlink()


@receiver(pre_delete, dispatch_uid='closure-model-delete')
def closure_model_delete(sender, **kwargs):
    if issubclass(sender, ClosureModel):
        instance = kwargs['instance']
        instance._closure_deletelink()
